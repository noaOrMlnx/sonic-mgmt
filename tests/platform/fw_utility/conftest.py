import os
import pytest
from fwutil_helper import *
from psu_controller import psu_controller
from loganalyzer import LogAnalyzer, LogAnalyzerError
import yaml

BASE_DIR = os.path.dirname(os.path.realpath(__file__))


@pytest.fixture(scope='function')
def get_fw_path(request, testbed_devices, components_list, component_object):
    """
    fixture that returns fw paths.
    :param request: request for binaries path entered by the user.
    :param testbed_devices
    :param components_list: list of components
    """
    dut = testbed_devices['dut']
    binaries_path = request.config.getoption("--binaries_path")
    if binaries_path is None:
        pytest.fail("Missing Arguments")
    yield component_object.process_versions(dut, components_list, binaries_path)


@pytest.fixture(scope='function')
def components_list(request, testbed_devices):
    """
    fixture that returns the components list
    according to the given config file.
    :param request
    :param testbed_devices: testbed devices
    """
    dut = testbed_devices['dut']
    config_file = request.config.getoption("--config_file")
    # config file contains platform string identifier and components separated by ','.
    # e.g.: x86_64-mlnx_msn2010-r0: BIOS,CPLD
    conf_path = os.path.join(BASE_DIR, config_file)
    with open(conf_path, "r") as config:
        platforms_dict = yaml.safe_load(config)
        platform_type = dut.facts['platform']
        components = platforms_dict[platform_type]

    yield components.split(",")


@pytest.fixture(scope='function')
def psu_ctrl(testbed_devices, psu_controller):
    """
    return psu_controller
    """
    dut = testbed_devices['dut']
    yield psu_controller(dut.hostname, dut.facts['asic_type'])


@pytest.fixture(scope='function')
def backup_platform_file(testbed_devices):
    """
    backup the original platform_components.json file
    """
    dut = testbed_devices['dut']
    platform_type = dut.facts['platform']
    platform_comp_path = '/usr/share/sonic/device/' + platform_type + '/platform_components.json'
    backup_path = os.path.join(BASE_DIR, "platform_component_backup.json")
    res = dut.fetch(src=platform_comp_path, dest=backup_path, flat="yes")

    yield

    dut.copy(src=backup_path, dest=platform_comp_path)


@pytest.fixture(scope='function')
def setup_images(request, testbed_devices, get_fw_path, components_list):
    """"
    setup part of 'update from next image test' case.
    backup both images files and generate new json files.
    """
    dut = testbed_devices['dut']
    set_next_boot(request, dut)
    image_info = get_image_info(dut)

    platform_type = dut.facts['platform']
    platform_comp_path = '/usr/share/sonic/device/' + platform_type + '/platform_components.json'

    # backup current image platform file
    current_backup_path = os.path.join(BASE_DIR, "current_platform_component_backup.json")
    res = dut.fetch(src=platform_comp_path, dest=current_backup_path, flat="yes")

    # reboot to next image
    reboot_to_image(request, testbed_devices, image_type=image_info['next'])
    # backup next-image platform file
    platform_type = dut.facts['platform']
    platform_comp_path = '/usr/share/sonic/device/' + platform_type + '/platform_components.json'
    next_backup_path = os.path.join(BASE_DIR, "next_platform_component_backup.json")
    dut.fetch(src=platform_comp_path, dest=next_backup_path, flat="yes")
    # generate component file for the next image
    current_component = get_fw_path['current_component']
    comp_path = os.path.join("/home/admin", current_component)
    dut.command("mkdir -p {}".format(comp_path))
    comp_path = os.path.join(comp_path, os.path.basename(get_fw_path['path_to_install']))
    generate_components_file(dut, components_list, get_fw_path['current_component'],
                                comp_path, get_fw_path['previous_ver'])
    # copy fw to dut (next image)
    dut.copy(src=get_fw_path['previous_ver'], dest=comp_path)

    # reboot to first image
    reboot_to_image(request, testbed_devices, image_type=image_info['current'])
    origin_img = image_info['current']
    next_img = image_info['next']

    yield
    # teardown
    new_image_info = get_image_info(dut)
    if new_image_info['current'] == next_img:
        dut.command("rm -rf {}".format(comp_path))
        # restore json file
        dut.copy(src=next_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
        reboot_to_image(request, testbed_devices, image_type=origin_img)
        dut.copy(src=current_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
    else:
        dut.copy(src=current_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
        reboot_to_image(request, testbed_devices, image_type=new_image_info['next'])
        dut.copy(src=next_backup_path, dest="/usr/share/sonic/device/{}/platform_components.json".format(platform_type))
        reboot_to_image(request, testbed_devices, image_type=new_image_info['current'])


@pytest.fixture(scope='function')
def component_object(components_list):
    current_comp = random.choice(components_list)
    current_comp = 'CPLD'
    yield globals()[current_comp.lower().capitalize() + 'Component']()
