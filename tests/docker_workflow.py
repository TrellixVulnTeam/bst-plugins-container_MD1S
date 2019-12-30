import filecmp
import json
import os

import pytest
from ruamel.yaml import YAML

from tests.utils import (
    build_and_checkout,
    push_image,
    load_image,
    untar,
    get_docker_host,
    create_element,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "project")


@pytest.mark.docker
@pytest.mark.datafiles(DATA_DIR)
def test_filesystem_equality(
    cli, datafiles, docker_client, docker_registry, tmp_path
):
    # pylint: disable=too-many-locals
    """
    Check that creating a docker_image from a base filesystem, and reimporting
    this image as a docker_source results in the same filesystem.
    """

    project = str(datafiles)
    file_system = os.path.join("files", "layers")
    yaml = YAML()
    yaml.default_flow_style = False
    client_to_registry = "{}:5000".format(get_docker_host(docker_client))

    # create example-tar.bst
    local_fs = "local-fs.bst"
    local_fs_element = {
        "kind": "import",
        "sources": [{"kind": "local", "path": "./{}".format(file_system)}],
    }
    create_element(yaml, local_fs, local_fs_element, project)

    image_name = "example-image"

    # create example-image.bst
    example_image = "example-image.bst"

    example_image_element = {
        "kind": "docker_image",
        "config": {
            "image-names": [
                "{}:latest".format(image_name),
                "bst-plugins-container-tests/{}:latest".format(image_name),
            ]
        },
        "build-depends": [local_fs],
    }
    create_element(yaml, example_image, example_image_element, project)

    # checkout and push image
    image_checkout_dir = os.path.join(str(tmp_path), "image_checkout")
    build_and_checkout(example_image, image_checkout_dir, cli, project)
    load_image(docker_client, image_checkout_dir)
    push_image(docker_client, docker_registry, image_name)

    # create docker source element
    docker_source = "example-image-source.bst"

    docker_source_element = {
        "kind": "import",
        "sources": [
            {
                "kind": "docker",
                "registry-url": "http://{}".format(client_to_registry),
                "image": image_name,
                "track": "latest",
            }
        ],
    }
    create_element(yaml, docker_source, docker_source_element, project)

    # source track Docker-sourced import element
    result = cli.run(project=project, args=["source", "track", docker_source])
    result.assert_success()

    # build Docker-sourced import element
    tar_checkout_dir = os.path.join(str(tmp_path), "tar_checkout")
    build_and_checkout(docker_source, tar_checkout_dir, cli, project)

    # assert files systems are equal
    layers_dir = os.path.join(project, "files", "layers")
    assert os.listdir(tar_checkout_dir) == os.listdir(layers_dir)
    # assert file systems have the same contents
    _compare_directory_files(layers_dir, tar_checkout_dir)


@pytest.mark.datafiles(DATA_DIR)
def test_image_equality(cli, datafiles, tmp_path):
    # pylint: disable=too-many-locals
    """
    Check that the filesystem of a rebuilt image built from an imported third-party image,
    results in the filesystem of the third-party image.
    """
    project = str(datafiles)
    yaml = YAML()
    yaml.default_flow_style = False

    # build and checkout hello-world image
    hello_world_source = "hello-world-image-source.bst"
    hello_world_source_element = {
        "kind": "import",
        "sources": [
            {
                "kind": "docker",
                "image": "library/hello-world",
                "track": "latest",
            }
        ],
    }
    create_element(
        yaml, hello_world_source, hello_world_source_element, project
    )

    hello_world_checkout_rel_dir = os.path.join("files", "hello-world")
    hello_world_checkout_dir = os.path.join(
        project, hello_world_checkout_rel_dir
    )
    result = cli.run(
        project=project, args=["source", "track", hello_world_source]
    )
    result.assert_success()
    build_and_checkout(
        hello_world_source, hello_world_checkout_dir, cli, project
    )

    # build image from extracted fs
    # create elements
    import_hello_world = "import-hello-world.bst"
    import_hello_world_element = {
        "kind": "import",
        "sources": [
            {
                "kind": "local",
                "path": "./{}".format(hello_world_checkout_rel_dir),
            }
        ],
    }
    create_element(
        yaml, import_hello_world, import_hello_world_element, project
    )

    hello_world_rebuild = "hello-world-image-rebuild.bst"
    hello_world_rebuild_element = {
        "kind": "docker_image",
        "config": {
            "image-names": [
                "bst-plugins-container-tests/hello-world-rebuild:latest"
            ]
        },
        "build-depends": [import_hello_world],
    }
    create_element(
        yaml, hello_world_rebuild, hello_world_rebuild_element, project
    )

    # build image
    rebuilt_image_checkout_dir = os.path.join(
        str(tmp_path), "rebuilt_image_checkout_dir"
    )
    build_and_checkout(
        hello_world_rebuild, rebuilt_image_checkout_dir, cli, project
    )

    # get layer filesystem
    untar_dir = untar(os.path.join(rebuilt_image_checkout_dir, "image.tar"))
    layer_dir = [
        os.path.join(untar_dir, layer_dir)
        for layer_dir in os.listdir(untar_dir)
        if os.path.isdir(os.path.join(untar_dir, layer_dir))
    ][0]
    layer_untar_dir = untar(os.path.join(layer_dir, "layer.tar"))

    # assert file systems are equal and have the same contents
    _compare_directory_files(layer_untar_dir, hello_world_checkout_dir)


def _get_config_digest(checkout_dir):
    manifest_file = os.path.join(checkout_dir, "manifest.json")
    with open(manifest_file) as manifest_file_handle:
        parsed_file = json.loads(manifest_file_handle.read())
    return parsed_file[0]["Config"].split(".")[0]


def _compare_directory_files(directory_a, directory_b):
    for (root_a, _, files), (root_b, _, _) in zip(
        os.walk(directory_a), os.walk(directory_b)
    ):
        for file in files:
            file_path_a = os.path.join(root_a, file)
            file_path_b = os.path.join(root_b, file)
            assert filecmp.cmp(file_path_a, file_path_b, shallow=False)
