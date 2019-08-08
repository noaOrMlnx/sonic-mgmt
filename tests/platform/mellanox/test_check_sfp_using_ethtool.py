"""
Check SFP using ethtool

This script covers the test case 'Check SFP using ethtool' in the SONiC platform test plan:
https://github.com/Azure/SONiC/blob/master/doc/pmon/sonic_platform_test_plan.md
"""
import logging
import os
import json

from ansible_host import AnsibleHost
from check_hw_mgmt_service import check_hw_management_service


def test_check_sfp_using_ethtool(localhost, ansible_adhoc, testbed):
    """This test case is to check SFP using the ethtool.
    """
    hostname = testbed['dut']
    ans_host = AnsibleHost(ansible_adhoc, hostname)
    localhost.command("who")
    lab_conn_graph_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), \
        "../../../ansible/files/lab_connection_graph.xml")
    conn_graph_facts = localhost.conn_graph_facts(host=hostname, filename=lab_conn_graph_file).\
        contacted["localhost"]["ansible_facts"]
    ports_config = json.loads(ans_host.command("sudo sonic-cfggen -d --var-json PORT")["stdout"])

    logging.info("Use the ethtool to check SFP information")
    for intf in conn_graph_facts["device_conn"]:
        intf_lanes = ports_config[intf]["lanes"]
        sfp_id = int(intf_lanes.split(",")[0])/4 + 1

        ethtool_sfp_output = ans_host.command("sudo ethtool -m sfp%s" % str(sfp_id))
        assert ethtool_sfp_output["rc"] == 0, "Failed to read eeprom of sfp%s using ethtool" % str(sfp_id)
        assert len(ethtool_sfp_output["stdout_lines"]) >= 5, \
            "Does the ethtool output look normal? " + str(ethtool_sfp_output["stdout_lines"])
        for line in ethtool_sfp_output["stdout_lines"]:
            assert len(line.split(":")) >= 2, \
                "Unexpected line %s in %s" % (line, str(ethtool_sfp_output["stdout_lines"]))

    logging.info("Check interface status")
    mg_facts = ans_host.minigraph_facts(host=hostname)["ansible_facts"]
    intf_facts = ans_host.interface_facts(up_ports=mg_facts["minigraph_ports"])["ansible_facts"]
    assert len(intf_facts["ansible_interface_link_down_ports"]) == 0, \
        "Some interfaces are down: %s" % str(intf_facts["ansible_interface_link_down_ports"])

    check_hw_management_service(ans_host)
