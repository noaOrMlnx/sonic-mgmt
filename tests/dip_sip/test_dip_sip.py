import os
import pytest
import ptf.testutils as testutils
import ptf.packet as packet
from ipaddress import ip_address

TOPO_LIST = {'t0', 't1', 't1-lag'}
PORTS_TOPO = {'t1'}
LAG_TOPO = {'t0', 't1-lag'}
DEFAULT_HLIM_TTL = 64
WAIT_EXPECTED_PACKET_TIMEOUT = 5


@pytest.fixture(scope='function', autouse=True)
def prepare_ptf(testbed_devices):
    ptfhost = testbed_devices["ptf"]
    # remove existing IPs from ptf host
    ptfhost.script('scripts/remove_ip.sh')
    # set unique MACs to ptf interfaces
    ptfhost.script('scripts/change_mac.sh')


def lag_facts(dut):
    pdb.set_trace()
    facts = {}
    mg_facts = dut.minigraph_facts(host=dut.hostname)['ansible_facts']
    if not mg_facts['minigraph_portchannels'] or len(mg_facts['minigraph_portchannels']) == 0:
        pytest.fail("minigraph_portchannels is not defined or zero length")
    host_facts = dut.setup()['ansible_facts']
    # minigraph facts
    src_lag = mg_facts['minigraph_portchannel_interfaces'][2]['attachto']
    dst_lag = mg_facts['minigraph_portchannel_interfaces'][0]['attachto']
    facts['src_port'] = src_lag
    facts['dst_port'] = dst_lag

    # lldp facts
    lldp_facts = dut.lldp()['ansible_facts']['lldp']
    facts['dst_host_mac'] = lldp_facts[mg_facts['minigraph_portchannels'][dst_lag]['members'][0]]['chassis']['mac']
    facts['src_host_mac'] = lldp_facts[mg_facts['minigraph_portchannels'][src_lag]['members'][0]]['chassis']['mac']
    facts['dst_router_mac'] = host_facts['ansible_' + dst_lag]['macaddress']
    facts['src_router_mac'] = host_facts['ansible_' + src_lag]['macaddress']
    facts['dst_router_ipv4'] = host_facts['ansible_' + dst_lag]['ipv4']['address']
    dst_ipv6 = host_facts['ansible_' + dst_lag]['ipv6']
    facts['dst_router_ipv6'] = [(item['address']) for item in dst_ipv6 if item['scope'] == 'global'][0]
    src_ipv6 = host_facts['ansible_' + src_lag]['ipv6']
    facts['dst_port_ids'] = [mg_facts['minigraph_port_indices'][mg_facts['minigraph_portchannels'][dst_lag]['members'][0]]]
    facts['src_port_ids'] = [mg_facts['minigraph_port_indices'][mg_facts['minigraph_portchannels'][src_lag]['members'][0]]]

    return facts


def port_facts(dut):
    facts = {}
    mg_facts = dut.minigraph_facts(host=dut.hostname)['ansible_facts']

    if not mg_facts['minigraph_interfaces'] or len(mg_facts['minigraph_interfaces']) == 0:
        pytest.fail("minigraph_interfaces is not defined or zero length")
    host_facts = dut.setup()['ansible_facts']
    # minigraph facts
    src_port = mg_facts['minigraph_interfaces'][2]['attachto']
    dst_port = mg_facts['minigraph_interfaces'][0]['attachto']
    facts['src_port'] = src_port
    facts['dst_port'] = dst_port
    # lldp facts
    lldp_facts = dut.lldp()['ansible_facts']['lldp']
    facts['dst_host_mac'] = lldp_facts[dst_port]['chassis']['mac']
    facts['src_host_mac'] = lldp_facts[src_port]['chassis']['mac']
    facts['dst_router_mac'] = host_facts['ansible_' + dst_port]['macaddress']
    facts['src_router_mac'] = host_facts['ansible_' + src_port]['macaddress']
    facts['dst_router_ipv4'] = host_facts['ansible_' + dst_port]['ipv4']['address']
    dst_ipv6 = host_facts['ansible_' + dst_port]['ipv6']
    facts['dst_router_ipv6'] = [(item['address']) for item in dst_ipv6 if item['scope'] == 'global'][0]
    src_ipv6 = host_facts['ansible_' + src_port]['ipv6']
    facts['dst_port_ids'] = [mg_facts['minigraph_port_indices'][dst_port]]
    facts['src_port_ids'] = [mg_facts['minigraph_port_indices'][src_port]]

    return facts


@pytest.fixture(scope='function')
def gather_facts(testbed_devices, testbed):
    facts = {}
    topo = testbed['topo']['name']
    if topo not in TOPO_LIST:
        pytest.skip("Unsupported topology")

    dut = testbed_devices["dut"]
    if topo in PORTS_TOPO:
        facts = port_facts(dut)
    elif topo in LAG_TOPO:
        facts = lag_facts(dut)
    else:
        pytest.skip("Unsupported topology")

    yield facts


def run_test_ipv6(ptfadapter, gather_facts):
    dst_host_ipv6 = str(ip_address(unicode(gather_facts['dst_router_ipv6']))+1)

    pkt = testutils.simple_udpv6_packet(
        eth_dst=gather_facts['src_router_mac'],
        eth_src=gather_facts['src_host_mac'],
        ipv6_src=dst_host_ipv6,
        ipv6_dst=dst_host_ipv6,
        ipv6_hlim=DEFAULT_HLIM_TTL

    )
    testutils.send(ptfadapter, int(gather_facts['src_port_ids'][0]), pkt)

    pkt = testutils.simple_udpv6_packet(
        eth_dst=gather_facts['dst_host_mac'],
        eth_src=gather_facts['dst_router_mac'],
        ipv6_src=dst_host_ipv6,
        ipv6_dst=dst_host_ipv6,
        ipv6_hlim=DEFAULT_HLIM_TTL-1
    )
    port_list = [int(port) for port in gather_facts['dst_port_ids']]
    testutils.verify_packet_any_port(ptfadapter, pkt, port_list, timeout=WAIT_EXPECTED_PACKET_TIMEOUT)


def run_test_ipv4(ptfadapter, gather_facts):
    dst_host_ipv4 = str(ip_address(unicode(gather_facts['dst_router_ipv4'])) + 1)

    pkt = testutils.simple_udp_packet(
        eth_dst=gather_facts['src_router_mac'],
        eth_src=gather_facts['src_host_mac'],
        ip_src=dst_host_ipv4,
        ip_dst=dst_host_ipv4,
        ip_ttl=DEFAULT_HLIM_TTL

    )
    testutils.send(ptfadapter, int(gather_facts['src_port_ids'][0]), pkt)

    pkt = testutils.simple_udp_packet(
        eth_dst=gather_facts['dst_host_mac'],
        eth_src=gather_facts['dst_router_mac'],
        ip_src=dst_host_ipv4,
        ip_dst=dst_host_ipv4,
        ip_ttl=DEFAULT_HLIM_TTL-1
    )
    port_list = [int(port) for port in gather_facts['dst_port_ids']]
    testutils.verify_packet_any_port(ptfadapter, pkt, port_list, timeout=WAIT_EXPECTED_PACKET_TIMEOUT)


def test_dip_sip(request, gather_facts):
    pdb.set_trace()
    ptfadapter = request.getfixturevalue('ptfadapter')
    ptfadapter.reinit()

    run_test_ipv4(ptfadapter, gather_facts)
    run_test_ipv6(ptfadapter, gather_facts)
