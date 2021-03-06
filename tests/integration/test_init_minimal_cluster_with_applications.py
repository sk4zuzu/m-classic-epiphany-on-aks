"""Integration testing of the "init" lifecycle phase."""

import os
import sys
import pathlib
import tempfile
import docker
from azepi._helpers import select, q_kind, load_yaml


DOCKER_IMAGE_NAME = "epiphanyplatform/azepi:stage3"

VARIABLES = {
    "VMS_RSA_FILENAME": "vms_rsa",
}

STATE_FILE_MOCK = '''
kind: state
azbi:
  status: applied
  size: 4
  use_public_ip: false
  location: northeurope
  name: azbi
  address_space:
    - 10.0.0.0/16
  address_prefixes:
    - 10.0.1.0/24
  rsa_pub_path: /shared/vms_rsa.pub
  output:
    private_ips.value:
      - 10.0.1.4
      - 10.0.1.5
      - 10.0.1.6
      - 10.0.1.7
    public_ips.value: []
    rg_name.value: azbi-rg
    vm_names.value:
      - azbi-0
      - azbi-1
      - azbi-2
      - azbi-3
    vnet_name.value: azbi-vnet
'''


def _check_flag_k8s_as_cloud_service(documents):
    """Make sure code for handling managed Kubernetes is enabled."""

    cluster = select(documents,
                     q_kind("epiphany-cluster"),
                     exactly=1)

    return cluster["specification"]["cloud"]["k8s_as_cloud_service"] is True


def _check_virtual_machine_assignments(documents):
    """Make sure each component gets all required vms."""

    cluster = select(documents,
                     q_kind("epiphany-cluster"),
                     exactly=1)

    machines = select(documents,
                      q_kind("infrastructure/machine"))

    machine_names = {
        item["name"]
        for item in machines
    }

    return all(
        int(value["count"]) == len(value["machines"]) and all(
            str(name) in machine_names
            for name in value["machines"]
        )
        for _, value in cluster["specification"]["components"].items()
        if int(value["count"]) > 0
    )


def _check_if_component_configs_are_present(documents):
    """Make sure each enabled component has correspoding document attached."""

    cluster = select(documents,
                     q_kind("epiphany-cluster"),
                     exactly=1)

    return all(
        select(documents,
               q_kind("configuration/" + key),
               exactly=1) is not None
        for key, value in cluster["specification"]["components"].items()
        if int(value["count"]) > 0
    )


def _check_flag_use_local_image_registry(documents):
    """Make sure Epiphany's internal registry is not used anywhere."""

    applications = select(documents,
                          q_kind("configuration/applications"),
                          exactly=1)

    return all(
        item["use_local_image_registry"] is False
        for item in applications["specification"]["applications"]
    )


def _check_if_provider_is_defined(documents):
    """Make sure "provider" is defined for all documents."""

    return all(
        "provider" in item and item["provider"] == "any"
        for item in documents
    )


def test_init_minimal_cluster_with_applications():
    """Test init method for mininal cluster config with postgresql and applications enabled."""

    client = docker.from_env()

    with tempfile.TemporaryDirectory(dir="/shared/") as shared_dir_name:
        shared_dir = pathlib.Path(shared_dir_name)

        with (shared_dir / "state.yml").open("w") as stream:
            stream.write(STATE_FILE_MOCK)

        cache_dir_env = os.getenv("CACHE_DIR", "/shared")

        external_cache_dir = pathlib.Path(cache_dir_env).resolve()

        # Fix for Windows to avoid path like "/workdir/C:/github/m-generic-epiphany-on-aks/.cache"
        if not external_cache_dir.exists() and pathlib.PureWindowsPath(cache_dir_env).is_absolute():
            external_cache_dir = pathlib.Path(cache_dir_env)

        # Fix access rights to the docker socket file (needed in macOS)
        os.system("chmod ugo+rw /var/run/docker.sock")

        # Fix access rights so the image in test can read shared data
        os.system(
            "chown -R {HOST_UID}:{HOST_GID} /shared/".format(**os.environ))

        container = client.containers.run(
            DOCKER_IMAGE_NAME,
            auto_remove=False,
            detach=True,
            volumes={
                "/var/run/docker.sock": {
                    "bind": "/var/run/docker.sock",
                    "mode": "rw",
                },
                str(external_cache_dir / "shared" / shared_dir.name): {
                    "bind": "/shared/",
                    "mode": "rw",
                },
            },
            command=[
                "init",
                # Create KEY=VALUE style parameters (makefile-like)
                *map("=".join, VARIABLES.items()),
            ],
        )

        try:
            container.wait()
            stdout = container.logs(stdout=True, stderr=False)
            stderr = container.logs(stdout=False, stderr=True)
        finally:
            container.remove(force=True)

        print(stderr, file=sys.stderr)

    module_config = load_yaml(stdout)

    documents = load_yaml(module_config["azepi"]["config"])

    assert _check_flag_k8s_as_cloud_service(documents)

    assert _check_virtual_machine_assignments(documents)

    assert _check_if_component_configs_are_present(documents)

    assert _check_flag_use_local_image_registry(documents)

    assert _check_if_provider_is_defined(documents)
