import pytest
import os
import pprint
from loganalyzer import LogAnalyzer, LogAnalyzerError
import time
from random import randint
from log_messages import *
import logging 
logger = logging.getLogger(__name__)

DEFAULT_LOOP_RANGE = 10
DEFAULT_LOOP_DELAY = 10

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
DUT_TMP_DIR = os.path.join('tmp', os.path.basename(BASE_DIR))
FILES_DIR = os.path.join(BASE_DIR, 'files')
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

ACL_TABLE_TEMPLATE = 'acltb_table.j2'
ACL_RULES_FULL_TEMPLATE = 'acltb_test_rules.j2'
ACL_REMOVE_RULES_FILE = 'acl_rules_del.json'
ACL_RULE_PERSISTENT_FILE = 'acl_rule_persistent.json'
ACL_RULE_PERSISTENT_DEL_FILE = 'acl_rule_persistent-del.json'
ACL_RULE_PERSISTENT_J2 = 'acl_rule_persistent.json.j2'

DEFAULT_ACL_STAGE = 'ingress'
ACL_TABLE_NAME = 'DATAACL'

#########################################
############### ACL PART ################
#########################################



def setup_acl_rules(duthost, acl_setup):
    """
    setup rules on DUT
    :param dut: dut host
    :param setup: setup information
    :param acl_table: acl table creating fixture
    :return:
    """

    name = ACL_TABLE_NAME
    dut_conf_file_path = os.path.join(acl_setup['dut_tmp_dir'], 'acl_rules_{}.json'.format(name))

    logger.info('generating config for ACL rules, ACL table {}'.format(name))
    extra_vars = {
        'acl_table_name':  name,
    }

    logger.info('extra variables for ACL table:\n{}'.format(pprint.pformat(extra_vars)))
    duthost.host.options['variable_manager'].extra_vars.update(extra_vars)

    duthost.template(src=os.path.join(TEMPLATE_DIR, ACL_RULES_FULL_TEMPLATE),
                    dest=dut_conf_file_path)

    logger.info('applying {}'.format(dut_conf_file_path))
    duthost.command('config acl update full {}'.format(dut_conf_file_path))       


@pytest.fixture(scope='function')
def acl_setup(duthost):
    """
    setup fixture gathers all test required information from DUT facts and testbed
    :param duthost: DUT host object
    :return: dictionary with all test required information
    """  

    logger.info('creating temporary folder for test {}'.format(DUT_TMP_DIR))
    duthost.command("mkdir -p {}".format(DUT_TMP_DIR))

    setup_information = {
        'dut_tmp_dir': DUT_TMP_DIR,
    }

    logger.info('setup variables {}'.format(pprint.pformat(setup_information)))

    yield setup_information

    logger.info('removing {}'.format(DUT_TMP_DIR))
    duthost.command('rm -rf {}'.format(DUT_TMP_DIR))


def teardown_acl(dut):
    """
    teardown ACL rules after test by applying empty configuration
    :param dut: DUT host object
    :param setup: setup information
    :return:
    """
    dst = DUT_TMP_DIR
    logger.info('removing all ACL rules')
    # copy rules remove configuration
    dut.copy(src=os.path.join(FILES_DIR, ACL_REMOVE_RULES_FILE), dest=dst)
    remove_rules_dut_path = os.path.join(dst, ACL_REMOVE_RULES_FILE)
    # remove rules
    logger.info('applying {}'.format(remove_rules_dut_path))
    dut.command('config acl update full {}'.format(remove_rules_dut_path))


@pytest.fixture(scope='function')
def acl(duthost, acl_setup):
    """
    setup/teardown ACL rules based on test class requirements
    :param duthost: DUT host object
    :param acl_setup: setup information
    :return:
    """
    loganalyzer = LogAnalyzer(ansible_host=duthost, marker_prefix='acl')
    loganalyzer.load_common_config()

    try:
        loganalyzer.expect_regex = [LOG_EXPECT_ACL_RULE_CREATE_RE]
        with loganalyzer:
            setup_acl_rules(duthost, acl_setup)
    except LogAnalyzerError as err:
        # cleanup config DB in case of log analysis error
        teardown_acl(duthost)
        raise err    

    try:
        yield
    finally:
        loganalyzer.expect_regex = [LOG_EXPECT_ACL_RULE_REMOVE_RE]
        with loganalyzer:
            teardown_acl(duthost)




#########################################
########### MIRRORING PART ##############
#########################################


MIRROR_RUN_DIR = os.path.join('mirror_tmp', os.path.basename(BASE_DIR))
EVERFLOW_TABLE_NAME = "EVERFLOW"
SESSION_NAME = "test_session_1"
session_info = {
    'name' : SESSION_NAME,
    'src_ip' : "1.1.1.1",
    'dst_ip' : "2.2.2.2",
    'ttl' : "1",
    'dscp' : "8",
    'gre' : "0x6558",
    'queue' : "0"
}

@pytest.fixture(scope='function')
def mirror_setup(duthost):
    """
    setup fixture
    """
    
    logger.debug("creating running directory ...")
    duthost.command('mkdir -p {}'.format(MIRROR_RUN_DIR))

    setup_info = {
        'dut_tmp_dir': MIRROR_RUN_DIR,
    }
    logger.info('setup variables {}'.format(pprint.pformat(setup_info)))
    yield setup_info
    
    teardown_mirroring(duthost)


@pytest.fixture(scope='function')
def mirroring(duthost, mirror_setup):
    """
    fixture gathers all configuration fixtures
    :param duthost: dut host
    :param mirror_setup: mirror_setup fixture 
    :param mirror_config: mirror_config fixture
    """

    logger.info("Adding mirror_session to dut")
    acl_rule_file = os.path.join(MIRROR_RUN_DIR, ACL_RULE_PERSISTENT_FILE)
    extra_vars = {
        'acl_table_name':  EVERFLOW_TABLE_NAME,    
    }
    logger.info('extra variables for MIRROR table:\n{}'.format(pprint.pformat(extra_vars)))
    duthost.host.options['variable_manager'].extra_vars.update(extra_vars)

    duthost.template(src=os.path.join(TEMPLATE_DIR, ACL_RULE_PERSISTENT_J2), dest=acl_rule_file)
    
    duthost.command('config mirror_session add {} {} {} {} {} {} {}'
    .format(session_info['name'], session_info['src_ip'], session_info['dst_ip'],
     session_info['dscp'], session_info['ttl'], session_info['gre'], session_info['queue']))

    logger.info('Loading acl mirror rules ...')
    load_rule_cmd = "acl-loader update full {} --session_name={}".format(acl_rule_file, session_info['name']) 
    duthost.command('{}'.format(load_rule_cmd))



def teardown_mirroring(dut):
    """
    teardown EVERFLOW rules after test by applying empty configuration
    :param dut: DUT host object
    :param setup: setup information
    :return:
    """
    logger.info('removing MIRRORING rules')
    # copy rules remove configuration
    dst = os.path.join(MIRROR_RUN_DIR, ACL_RULE_PERSISTENT_DEL_FILE)
    dut.copy(src=os.path.join(FILES_DIR, ACL_RULE_PERSISTENT_DEL_FILE), dest=dst)
    dut.command("acl-loader update full {}".format(dst))
    dut.command('config mirror_session remove {}'.format(SESSION_NAME))
    dut.command('rm -rf {}'.format(MIRROR_RUN_DIR))



@pytest.fixture(scope='function', params=['acl', 'mirroring'], autouse=True)
def config(request):
    """
    fixture to add configurations on setup by received parameters.
    """
    return request.getfixturevalue(request.param)



def test_techsupport(request, config, duthost, testbed):
    """
    test the "show techsupport" command in a loop
    :param config: fixture to configure additional setups_list on dut.
    :param duthost: dut host
    :param testbed: testbed
    """
    loop_range = request.config.getoption("--loop_num") or DEFAULT_LOOP_RANGE
    loop_delay = request.config.getoption("--loop_delay") or DEFAULT_LOOP_DELAY
    since = request.config.getoption("--logs_since") or randint(1, 60)
    
    logger.debug("loop range is {} and loop delay is {}".format(loop_range, loop_delay))

    for i in range(loop_range):
        logger.debug("Running show techsupport ... ")
        duthost.command("show techsupport --since='{} minute ago'".format(since))
        logger.debug("Sleeping for {} seconds".format(loop_delay))
        time.sleep(loop_delay)
