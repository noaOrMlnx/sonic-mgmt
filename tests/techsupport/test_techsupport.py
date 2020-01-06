import pytest
import os
import pprint
from loganalyzer import LogAnalyzer, LogAnalyzerError
from common import reboot, port_toggle
import pdb
import optparse
import time
from random import randint
import logging 
logger = logging.getLogger(__name__)

LOOP_RANGE = 10
LOOP_DELAY=10
rules_list = ['acl_rules']
teardown_list = []



BASE_DIR = os.path.dirname(os.path.realpath(__file__))
DUT_TMP_DIR = os.path.join('tmp', os.path.basename(BASE_DIR))
FILES_DIR = os.path.join(BASE_DIR, 'files')
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

ACL_TABLE_TEMPLATE = 'acltb_table.j2'
ACL_RULES_FULL_TEMPLATE = 'acltb_test_rules.j2'
ACL_RULES_PART_TEMPLATES = tuple('acltb_test_rules_part_{}.j2'.format(i) for i in xrange(1, 3))
ACL_REMOVE_RULES_FILE = 'acl_rules_del.json'
ACL_RULE_PERSISTENT_FILE = 'acl_rule_persistent.json'
ACL_RULE_PERSISTENT_DEL_FILE = 'acl_rule_persistent-del.json'
ACL_RULE_PERSISTENT_J2 = 'acl_rule_persistent.json.j2'

pytestmark = [
    pytest.mark.acl,
    pytest.mark.disable_loganalyzer  # disable automatic loganalyzer
]
#########################################
############### ACL PART ################
#########################################


DST_IP_TOR = '172.16.1.0'
DST_IP_TOR_FORWARDED = '172.16.2.0'
DST_IP_TOR_BLOCKED = '172.16.3.0'
DST_IP_SPINE = '192.168.0.0'
DST_IP_SPINE_FORWARDED = '192.168.0.16'
DST_IP_SPINE_BLOCKED = '192.168.0.17'

LOG_EXPECT_ACL_TABLE_CREATE_RE = '.*Created ACL table.*'
LOG_EXPECT_ACL_TABLE_REMOVE_RE = '.*Successfully deleted ACL table.*'
LOG_EXPECT_ACL_RULE_CREATE_RE = '.*Successfully created ACL rule.*'
LOG_EXPECT_ACL_RULE_REMOVE_RE = '.*Successfully deleted ACL rule.*'


def setup_acl_rules(duthost, acl_setup, acl_table):
        """
        setup rules on DUT
        :param dut: dut host
        :param setup: setup information
        :param acl_table: acl table creating fixture
        :return:
        """
        name = acl_table['name']
        dut_conf_file_path = os.path.join(acl_setup['dut_tmp_dir'], 'acl_rules_{}.json'.format(name))

        logger.info('generating config for ACL rules, ACL table {}'.format(name))
        duthost.template(src=os.path.join(TEMPLATE_DIR, ACL_RULES_FULL_TEMPLATE),
                     dest=dut_conf_file_path)

        logger.info('applying {}'.format(dut_conf_file_path))
        duthost.command('config acl update full {}'.format(dut_conf_file_path))


@pytest.fixture(scope='module')
def acl_rules(duthost, localhost, acl_setup, acl_table):

    """
    setup/teardown ACL rules based on test class requirements
    :param duthost: DUT host object
    :param localhost: localhost object
    :param setup: setup information
    :param acl_table: table creating fixture
    :return:
    """
    loganalyzer = LogAnalyzer(ansible_host=duthost, marker_prefix='acl')
    loganalyzer.load_common_config()

    try:
        loganalyzer.expect_regex = [LOG_EXPECT_ACL_RULE_CREATE_RE]
        with loganalyzer:
            setup_acl_rules(duthost, acl_setup, acl_table)
    except LogAnalyzerError as err:
        # cleanup config DB in case of log analysis error
        teardown_acl(duthost, acl_setup)
        raise err    

    try:
        yield
    finally:
        loganalyzer.expect_regex = [LOG_EXPECT_ACL_RULE_REMOVE_RE]
        with loganalyzer:
            teardown_acl(duthost, acl_setup)



@pytest.fixture(scope='module')
def acl_setup(duthost, testbed):
    """
    setup fixture gathers all test required information from DUT facts and testbed
    :param duthost: DUT host object
    :param testbed: Testbed object
    :return: dictionary with all test required information
    """  
    # pdb.set_trace()
    ports = []
    port_channels = []
    acl_table_ports = []

    # pdb.set_trace()

    mg_facts = duthost.minigraph_facts(host=duthost.hostname)['ansible_facts']

    for dut_port, neigh in mg_facts['minigraph_neighbors'].items():
        if 'T0' in neigh['name'] or 'T2' in neigh['name']:
            ports.append(dut_port)


    # get the list of port channels
    port_channels = mg_facts['minigraph_portchannels']
    acl_table_ports += ports
    acl_table_ports += port_channels

    logger.info('creating temporary folder for test {}'.format(DUT_TMP_DIR))
    duthost.command("mkdir -p {}".format(DUT_TMP_DIR))

    host_facts = duthost.setup()['ansible_facts']

    setup_information = {
        # 'router_mac': host_facts['ansible_Ethernet0']['macaddress'],
        'dut_tmp_dir': DUT_TMP_DIR,
        'port_channels': port_channels,
        'acl_table_ports': acl_table_ports,
        # 'dst_ip_tor': DST_IP_TOR,
        # 'dst_ip_tor_forwarded': DST_IP_TOR_FORWARDED,
        # 'dst_ip_tor_blocked': DST_IP_TOR_BLOCKED,
        # 'dst_ip_spine': DST_IP_SPINE,
        # 'dst_ip_spine_forwarded': DST_IP_SPINE_FORWARDED,
        # 'dst_ip_spine_blocked': DST_IP_SPINE_BLOCKED,
    }

    logger.info('setup variables {}'.format(pprint.pformat(setup_information)))

    yield setup_information

    logger.info('removing {}'.format(DUT_TMP_DIR))
    duthost.command('rm -rf {}'.format(DUT_TMP_DIR))


@pytest.fixture(scope='module')
def stage(request):
    """
    small fixture to parametrize test for ingres/egress stage testing
    :param request: pytest request
    :return: stage parameter
    """

    return 'ingress'


@pytest.fixture(scope='module')
def acl_table_config(duthost, acl_setup, stage):

    """
    generate ACL table configuration files and deploy them on DUT;
    after test run cleanup artifacts on DUT
    :param duthost: DUT host object
    :param setup: setup parameters
    :param stage: stage
    :return: dictionary of table name and matching configuration file
    """

    # Initialize data for ACL tables
    tables_map = {
        'ingress': 'DATAINGRESS',
        'egress': 'DATAEGRESS',
    }
    acl_table_name = tables_map[stage]
    tmp_dir = acl_setup['dut_tmp_dir']

    acl_table_vars = {
        'acl_table_name':  acl_table_name,
        'acl_table_ports': acl_setup['acl_table_ports'],
        'acl_table_stage': stage,
        'acl_table_type': 'L3',
    }

    logger.info('extra variables for ACL table:\n{}'.format(pprint.pformat(acl_table_vars)))
    duthost.host.options['variable_manager'].extra_vars.update(acl_table_vars)

    logger.info('generate config for ACL table {}'.format(acl_table_name))
    acl_config = 'acl_table_{}.json'.format(acl_table_name)
    acl_config_path = os.path.join(tmp_dir, acl_config)
    # pdb.set_trace()
    duthost.template(src=os.path.join(TEMPLATE_DIR, ACL_TABLE_TEMPLATE), dest=acl_config_path)

    yield {
        'name': acl_table_name,
        'config_file': acl_config_path,
    }


@pytest.fixture(scope='module')
def acl_table(duthost, acl_table_config):
    """
    fixture to apply ACL table configuration and remove after tests
    :param duthost: DUT object
    :param acl_table_config: ACL table configuration dictionary
    :return: forwards acl_table_config
    """

    name = acl_table_config['name']
    conf = acl_table_config['config_file']
    loganalyzer = LogAnalyzer(ansible_host=duthost, marker_prefix='acl_rules')
    loganalyzer.load_common_config()

    try:
        pdb.set_trace()
        loganalyzer.expect_regex = [LOG_EXPECT_ACL_TABLE_CREATE_RE]
        with loganalyzer:
            logger.info('creating ACL table: applying {}'.format(conf))
            duthost.command('sonic-cfggen -j {} --write-to-db'.format(conf))
    except LogAnalyzerError as err:
        # cleanup config DB if create failed
        duthost.command('config acl remove table {}'.format(name))
        raise err

    try:
        yield acl_table_config
    finally:
        loganalyzer.expect_regex = [LOG_EXPECT_ACL_TABLE_REMOVE_RE]
        with loganalyzer:
            logger.info('removing ACL table {}'.format(name))

            duthost.command('config acl remove table {}'.format(name))

        # save cleaned configuration
        duthost.command('config save -y')


def teardown_acl(dut, acl_setup):
    """
    teardown ACL rules after test by applying empty configuration
    :param dut: DUT host object
    :param setup: setup information
    :return:
    """
    pdb.set_trace()
    logger.info('removing all ACL rules')
    # copy rules remove configuration
    dut.copy(src=os.path.join(FILES_DIR, ACL_REMOVE_RULES_FILE), dest=acl_setup['dut_tmp_dir'])
    remove_rules_dut_path = os.path.join(acl_setup['dut_tmp_dir'], ACL_REMOVE_RULES_FILE)
    # remove rules
    logger.info('applying {}'.format(remove_rules_dut_path))
    dut.command('config acl update full {}'.format(remove_rules_dut_path))


@pytest.fixture(scope='module')
def acl(duthost, acl_setup, acl_table):
    """
    setup rules on DUT
        :param dut: dut host
        :param acl_setup: setup information
        :param acl_table: acl table creating fixture
        :param acl_rules acl rules setup fixture
        :return:
    """

    name = acl_table['name']
    dut_conf_file_path = os.path.join(acl_setup['dut_tmp_dir'], 'acl_rules_{}.json'.format(name))

    logger.info('generating config for ACL rules, ACL table {}'.format(name))
    duthost.template(src=os.path.join(TEMPLATE_DIR, ACL_RULES_FULL_TEMPLATE),
                 dest=dut_conf_file_path)

    logger.info('applying {}'.format(dut_conf_file_path))
    duthost.command('config acl update full {}'.format(dut_conf_file_path))




#########################################
########### MIRRORING PART ##############
#########################################


MIRROR_RUN_DIR = os.path.join('mirror_tmp', os.path.basename(BASE_DIR))
ACL_TABLE_NAME = "EVERFLOW"
DEFAULT_MIRROR_STAGE = "ingress"

LOG_EXCEPT_EVERFLOW_TABLE_CREATE = '.*Create ACL Group member*.'
LOG_EXCEPT_EVERFLOW_TABLE_DELETE = '.*Successfully deleted ACL table*.'
LOG_EXCEPT_MIRROR_SESSION_REMOVE = '.*Removed mirror session*.'
SESSION_NAME = "test_session_1"

tables_map = {
    'ingress': 'DATAINGRESS',
    'egress': 'DATAEGRESS',
}


@pytest.fixture(scope='module')
def mirror_table_config(request, duthost, mirror_setup):

    mirror_stage = DEFAULT_MIRROR_STAGE
    if request.config.getoption("--mirror_stage"):
        mirror_stage = request.config.getoption("--mirror_stage")
        if not mirror_stage in tables_map.keys():
            logger.info("Mirorr stage is not valid, default is ingress.")
            mirror_stage = DEFAULT_MIRROR_STAGE

    # DATAINGRESS / DATAEGRESS
    everflow_table_name = tables_map[mirror_stage]
    tmp_dir = mirror_setup['dut_tmp_dir']


    acl_table_vars = {
        'acl_table_name':  ACL_TABLE_NAME,
        'acl_table_ports': mirror_setup['acl_table_ports'],
        'acl_table_stage': mirror_stage,
        'acl_table_type': 'MIRROR',
    }
    
    logger.info('extra variables for MIRROR table:\n{}'.format(pprint.pformat(acl_table_vars)))
    duthost.host.options['variable_manager'].extra_vars.update(acl_table_vars)

    logger.info('generate config for MIRROR table {}'.format(everflow_table_name))
    src = os.path.join(TEMPLATE_DIR, ACL_TABLE_TEMPLATE)
    everflow_config = 'everflow_table_' + everflow_table_name + '.json'
    everflow_config_path = os.path.join(tmp_dir, everflow_config)
    duthost.template(src=src, dest=everflow_config_path)

    # yield {
    #     'name' : everflow_table_name,
    #     'config_file' : everflow_config_path 
    # }
    yield {
        'name' : ACL_TABLE_NAME,
        'config_file' : everflow_config_path 
    }



@pytest.fixture(scope='module')
def mirror_table(duthost, mirror_table_config):
    """
    generate everflow table with acl_stage=ingress, mirror_stage=ingress
    """

    pdb.set_trace()

    name = mirror_table_config['name']
    conf = mirror_table_config['config_file']
    loganalyzer = LogAnalyzer(ansible_host=duthost, marker_prefix='acl')
    loganalyzer.load_common_config()


    pdb.set_trace()
    logger.info('creating MIRROR table: applying {}'.format(name))
    duthost.command('sonic-cfggen -j {} --write-to-db'.format(conf))

    # try:
    #     yield mirror_table_config
    # finally:
    #     logger.info('removing EVERFLOW table {}'.format(name))
    #     duthost.command('config acl remove table {}'.format(name))
    #     duthost.command('config save -y')



    try:
        pdb.set_trace()
        loganalyzer.expect_regex = [LOG_EXCEPT_EVERFLOW_TABLE_CREATE]
        with loganalyzer:
            logger.info('creating MIRROR table: applying {}'.format(name))
            duthost.command('sonic-cfggen -j {} --write-to-db'.format(conf))
    except LogAnalyzerError as err:
        logger.info('removing EVERFLOW table {}'.format(name))
        duthost.command('config acl remove table {}'.format(name))
        raise err
    
    try:
        yield mirror_table_config
    finally:
        loganalyzer.expect_regex = [LOG_EXCEPT_EVERFLOW_TABLE_DELETE]
        with loganalyzer:
            logger.info('removing EVERFLOW table {}'.format(name))
            duthost.command('config acl remove table {}'.format(name))
        duthost.command('config save -y')


@pytest.fixture(scope='module')
def mirror_setup(duthost):
    
    # pdb.set_trace()
    ports = []
    port_channels = []
    acl_table_ports = []

    mg_facts = duthost.minigraph_facts(host=duthost.hostname)['ansible_facts']

    for dut_port, neigh in mg_facts['minigraph_neighbors'].items():
        if 'T0' in neigh['name'] or 'T2' in neigh['name']:
            ports.append(dut_port)

    # get the list of port channels
    port_channels = mg_facts['minigraph_portchannels']
    acl_table_ports += ports
    acl_table_ports += port_channels
    
    # remove default SONiC Everflow table (allowed to have only one mirror table)
    duthost.command('config acl remove table EVERFLOW')
    # pdb.set_trace()
    logger.debug("creating running directory ...")
    duthost.command('mkdir -p {}'.format(MIRROR_RUN_DIR))

    setup_info = {
        'dut_tmp_dir': MIRROR_RUN_DIR,
        'port_channels': port_channels,
        'acl_table_ports': acl_table_ports,
    }
    logger.info('setup variables {}'.format(pprint.pformat(setup_info)))
    yield setup_info

    logger.info('removing {}'.format(MIRROR_RUN_DIR))
    duthost.command('rm -rf {}'.format(MIRROR_RUN_DIR))


@pytest.fixture(scope='module')
def session_info():
    yield {
    'name' : SESSION_NAME,
    'src_ip' : "1.1.1.1",
    'dst_ip' : "2.2.2.2",
    'ttl' : "1",
    'dscp' : "8",
    'gre' : "0x6558",
    'queue' : "0" 
    }


@pytest.fixture(scope='module')
def mirror_config(duthost, session_info):
    pdb.set_trace()

    logger.info("Adding mirror_session to dut")
    duthost.template(src=os.path.join(TEMPLATE_DIR, ACL_RULE_PERSISTENT_J2), dest=os.path.join(FILES_DIR, ACL_RULE_PERSISTENT_FILE))
    duthost.command('config mirror_session add {} {} {} {} {} {} {}'
    .format(session_info['name'], session_info['src_ip'], session_info['dst_ip'],
     session_info['dscp'], session_info['ttl'], session_info['gre'], session_info['queue']))

    logger.info('Loading acl mirror rules ...')
    load_rule_cmd = "acl-loader update full {} --session_name={}".format(os.path.join(FILES_DIR, ACL_RULE_PERSISTENT_FILE), session_info['name']) 
    if DEFAULT_MIRROR_STAGE == "egress":
        load_rule_cmd += '--mirror_stage=egress'
    
    duthost.command('{}'.format(load_rule_cmd))


@pytest.fixture(scope='module')
def mirroring(request, duthost, mirror_setup, mirror_table, mirror_config):
    logger.info('Configured Everflow')


def teardown_mirroring(dut, mirror_setup):
    """
    teardown EVERFLOW rules after test by applying empty configuration
    :param dut: DUT host object
    :param setup: setup information
    :return:
    """
    pdb.set_trace()
    logger.info('removing MIRRORING rules')
    # copy rules remove configuration
    dut.copy(src=os.path.join(FILES_DIR, ACL_RULE_PERSISTENT_DEL_FILE), dest=MIRROR_RUN_DIR)
    dut.command("acl-loader update full {}".format(os.path.join(FILES_DIR, ACL_RULE_PERSISTENT_DEL_FILE)))
    dut.command('config mirror_session remove {}'.format(SESSION_NAME))
    dut.command('rm -rf {}'.format(MIRROR_RUN_DIR))

setups_list = {
    teardown_acl : acl_setup,
    teardown_mirroring : mirror_setup
}
teardown_dict = {
    'acl' : teardown_acl,
    'mirroring' : teardown_mirroring
}


@pytest.fixture(scope='module', params=['acl', 'mirroring'])
def config(request):
    """
    fixture to add configurations on setup by received parameters.
    """
    # pdb.set_trace()
    fixt_rule = request.param + '_rules'
    teardown_list.append(teardown_dict[request.param])
    if fixt_rule in rules_list:
        request.getfixturevalue(fixt_rule)
    return request.getfixturevalue(request.param)


def test_techsupport(request, config, duthost, testbed):
    """
    test the "show techsupport" command in a loop
    :param config: fixture to configure additional setups_list on dut.
    :param duthost: dut host
    :param testbed: testbed
    """
    pdb.set_trace()
    loop_range = LOOP_RANGE
    loop_delay = LOOP_DELAY
    if request.config.getoption("--loop_num"):
        loop_range = request.config.getoption("--loop_num")
    if request.config.getoption("--loop_delay"):
        loop_delay = request.config.getoption("--loop_delay")
    if request.config.getoption("--logs_since"):
        since = request.config.getoption("--logs_since")
    else:
        since = randint(1, 60)
    logger.debug("loop range is {} and loop delay is {}".format(loop_range, loop_delay))

    for i in range(loop_range):
        logger.debug("Running show techsupport ... ")
        duthost.command("show techsupport --since='{} minute ago'".format(since))
        logger.debug("Sleeping for {} seconds".format(loop_delay))
        time.sleep(loop_delay)

    pdb.set_trace()
    # clean all additional configurations
    for teardown in teardown_list:
        logger.debug("teardown loop")
        teardown(duthost, setups_list[teardown])
        
