# Copyright 2016 F5 Networks Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


from conftest import remove_elements
from conftest import setup_neutronless_test
from copy import deepcopy
from f5.utils.testutils.registrytools import register_device
from f5_openstack_agent.lbaasv2.drivers.bigip.icontrol_driver import \
    iControlDriver
import json
import logging
import mock
from mock import call
import pytest
import requests
import time

requests.packages.urllib3.disable_warnings()

LOG = logging.getLogger(__name__)

OSLO_CONFIGS = json.load(open('vcmp_oslo_confs.json'))
VCMP_CONFIG = OSLO_CONFIGS["vcmp_single_host"]

# Toggle feature on/off configurations
OSLO_CONFIGS = json.load(open('oslo_confs.json'))
FEATURE_ON = OSLO_CONFIGS["feature_on"]
FEATURE_OFF = OSLO_CONFIGS["feature_off"]


# Library of services as received from the neutron server
NEUTRON_SERVICES = json.load(open('neutron_services.json'))
SEGID_CREATELB = NEUTRON_SERVICES["create_connected_loadbalancer"]
NOSEGID_CREATELB = NEUTRON_SERVICES["create_disconnected_loadbalancer"]
SEGID_CREATELISTENER = NEUTRON_SERVICES["create_connected_listener"]
NOSEGID_CREATELISTENER = NEUTRON_SERVICES["create_disconnected_listener"]

# BigIP device states observed via f5sdk.
AGENT_INIT_URIS = \
    set([u'https://localhost/mgmt/tm/net/tunnels/vxlan/'
         '~Common~vxlan_ovs?ver=11.6.0',

         u'https://localhost/mgmt/tm/net/tunnels/gre/'
         '~Common~gre_ovs?ver=11.6.0'])

SEG_INDEPENDENT_LB_URIS =\
    set([u'https://localhost/mgmt/tm/sys/folder/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d?ver=11.6.0',

         u'https://localhost/mgmt/tm/net/route-domain/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d?ver=11.6.0',

         u'https://localhost/mgmt/tm/net/fdb/tunnel/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~disconnected_network?ver=11.5.0',

         u'https://localhost/mgmt/tm/net/tunnels/tunnel/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~disconnected_network?ver=11.6.0'])

SEG_DEPENDENT_LB_URIS =\
    set([u'https://localhost/mgmt/tm/ltm/snat-translation/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~snat-traffic-group-local-only'
         '-ce69e293-56e7-43b8-b51c-01b91d66af20_0?ver=11.6.0',

         u'https://localhost/mgmt/tm/ltm/snatpool/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d?ver=11.6.0',

         u'https://localhost/mgmt/tm/net/fdb/tunnel/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d~tunnel-vxlan-46?ver=11.5.0',

         u'https://localhost/mgmt/tm/net/self/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~local-bigip1-ce69e293-56e7-43b8-b51c-01b91d66af20?ver=11.6.0',

         u'https://localhost/mgmt/tm/net/tunnels/tunnel/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~tunnel-vxlan-46?ver=11.6.0'])

SEG_LISTENER_URIS = \
    set([u'https://localhost/mgmt/tm/ltm/virtual-address/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~10.2.2.140%251?ver=11.6.0',

         u'https://localhost/mgmt/tm/ltm/virtual/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~SAMPLE_LISTENER?ver=11.6.0'])

NOSEG_LISTENER_URIS =\
    set([u'https://localhost/mgmt/tm/ltm/virtual-address/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~10.2.2.140?ver=11.6.0',

         u'https://localhost/mgmt/tm/ltm/virtual/'
         '~TEST_128a63ef33bc4cf891d684fad58e7f2d'
         '~SAMPLE_LISTENER?ver=11.6.0'])

ERROR_MSG_MISCONFIG = 'Misconfiguration: Segmentation ID is missing'
ERROR_MSG_VXLAN_TUN = 'Failed to create vxlan tunnel:'
ERROR_MSG_GRE_TUN = 'Failed to create gre tunnel:'
ERROR_MSG_TIMEOUT = 'TIMEOUT: failed to connect '


def create_default_mock_rpc_plugin():
    mock_rpc_plugin = mock.MagicMock(name='mock_rpc_plugin')
    mock_rpc_plugin.get_port_by_name.return_value = [
        {'fixed_ips': [{'ip_address': '10.2.2.134'}]}
    ]
    return mock_rpc_plugin


def configure_icd(icd_config, create_mock_rpc):
    class ConfFake(object):
        '''minimal fake config object to replace oslo with controlled params'''
        def __init__(self, params):
            self.__dict__ = params
            for k, v in self.__dict__.items():
                if isinstance(v, unicode):
                    self.__dict__[k] = v.encode('utf-8')

        def __repr__(self):
            return repr(self.__dict__)

    icontroldriver = iControlDriver(ConfFake(icd_config),
                                    registerOpts=False)
    icontroldriver.plugin_rpc = create_mock_rpc()
    return icontroldriver


def logcall(lh, call, *cargs, **ckwargs):
    call(*cargs, **ckwargs)


@pytest.fixture
def setup_l2adjacent_test(request, bigip, makelogdir):
    loghandler = setup_neutronless_test(request, bigip, makelogdir)
    LOG.info('Test setup: %s' % request.node.name)

    try:
        remove_elements(bigip,
                        SEG_INDEPENDENT_LB_URIS |
                        SEG_DEPENDENT_LB_URIS |
                        SEG_LISTENER_URIS |
                        AGENT_INIT_URIS)
    finally:
        LOG.info('removing pre-existing config')

    return loghandler


def handle_init_registry(bigip, icd_configuration,
                         create_mock_rpc=create_default_mock_rpc_plugin):
    init_registry = register_device(bigip)
    icontroldriver = configure_icd(icd_configuration, create_mock_rpc)
    start_registry = register_device(bigip)
    assert set(start_registry.keys()) - set(init_registry.keys()) ==\
        AGENT_INIT_URIS
    return icontroldriver, start_registry


def test_featureoff_withsegid_lb(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_OFF)
    service = deepcopy(SEGID_CREATELB)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == SEG_INDEPENDENT_LB_URIS | SEG_DEPENDENT_LB_URIS
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.get_port_by_name.call_args_list == [
        call(port_name=u'local-bigip1-ce69e293-56e7-43b8-b51c-01b91d66af20'),
        call(port_name=u'snat-traffic-group-local-only-'
                       'ce69e293-56e7-43b8-b51c-01b91d66af20_0')
    ]
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'ONLINE')
    ]


def test_withsegid_lb(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_ON)
    service = deepcopy(SEGID_CREATELB)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == SEG_INDEPENDENT_LB_URIS | SEG_DEPENDENT_LB_URIS
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.get_port_by_name.call_args_list == [
        call(port_name=u'local-bigip1-ce69e293-56e7-43b8-b51c-01b91d66af20'),
        call(port_name=u'snat-traffic-group-local-only-'
                       'ce69e293-56e7-43b8-b51c-01b91d66af20_0')
    ]
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'ONLINE')
    ]


def test_featureoff_withsegid_listener(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_OFF)
    service = deepcopy(SEGID_CREATELISTENER)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == (SEG_INDEPENDENT_LB_URIS |
                           SEG_DEPENDENT_LB_URIS |
                           SEG_LISTENER_URIS)
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.get_port_by_name.call_args_list == [
        call(port_name=u'local-bigip1-ce69e293-56e7-43b8-b51c-01b91d66af20'),
        call(port_name=u'snat-traffic-group-local-only-'
                       'ce69e293-56e7-43b8-b51c-01b91d66af20_0')
    ]
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'ONLINE')
    ]
    assert rpc.update_listener_status.call_args_list == [
        call(u'105a227a-cdbf-4ce3-844c-9ebedec849e9', 'ACTIVE', 'ONLINE')
    ]


def test_featureoff_nosegid_lb(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_OFF)
    service = deepcopy(NOSEGID_CREATELB)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == SEG_INDEPENDENT_LB_URIS
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_MISCONFIG in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ERROR', 'OFFLINE')
    ]


def test_featureoff_nosegid_listener(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_OFF)
    service = deepcopy(NOSEGID_CREATELISTENER)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == SEG_INDEPENDENT_LB_URIS
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_MISCONFIG in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ERROR', 'OFFLINE')
    ]


def test_withsegid_listener(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_ON)
    service = deepcopy(SEGID_CREATELISTENER)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == (SEG_INDEPENDENT_LB_URIS |
                           SEG_DEPENDENT_LB_URIS |
                           SEG_LISTENER_URIS)
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.get_port_by_name.call_args_list == [
        call(port_name=u'local-bigip1-ce69e293-56e7-43b8-b51c-01b91d66af20'),
        call(port_name=u'snat-traffic-group-local-only-'
                       'ce69e293-56e7-43b8-b51c-01b91d66af20_0')
    ]
    assert rpc.update_listener_status.call_args_list == [
        call(u'105a227a-cdbf-4ce3-844c-9ebedec849e9', 'ACTIVE', 'ONLINE')
    ]
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'ONLINE')
    ]


def test_nosegid_lb(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_ON)
    service = deepcopy(NOSEGID_CREATELB)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == SEG_INDEPENDENT_LB_URIS
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'OFFLINE')
    ]


def test_nosegid_listener(setup_l2adjacent_test, bigip):
    icontroldriver, start_registry = handle_init_registry(bigip, FEATURE_ON)
    service = deepcopy(NOSEGID_CREATELISTENER)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    after_create_registry = register_device(bigip)
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    create_uris = (set(after_create_registry.keys()) -
                   set(start_registry.keys()))
    assert create_uris == SEG_INDEPENDENT_LB_URIS | NOSEG_LISTENER_URIS
    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    assert rpc.update_listener_status.call_args_list == [
        call(u'105a227a-cdbf-4ce3-844c-9ebedec849e9', 'ACTIVE', 'OFFLINE')
    ]
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'OFFLINE')
    ]


def test_nosegid_listener_timeout(setup_l2adjacent_test, bigip):
    def create_mock_rpc_plugin():
        mock_rpc_plugin = mock.MagicMock(name='mock_rpc_plugin')
        mock_rpc_plugin.get_port_by_name.return_value = [
            {'fixed_ips': [{'ip_address': '10.2.2.134'}]}
        ]
        mock_rpc_plugin.get_all_loadbalancers.return_value = [
            {'lb_id': u'50c5d54a-5a9e-4a80-9e74-8400a461a077'}
        ]
        service = deepcopy(NOSEGID_CREATELISTENER)
        service['loadbalancer']['provisioning_status'] = "ACTIVE"
        mock_rpc_plugin.get_service_by_loadbalancer_id.return_value = service
        return mock_rpc_plugin
    # Configure
    icontroldriver, start_registry = handle_init_registry(
        bigip, FEATURE_ON, create_mock_rpc_plugin)
    gtimeout = icontroldriver.conf.f5_network_segment_gross_timeout
    poll_interval = icontroldriver.conf.f5_network_segment_polling_interval
    service = deepcopy(NOSEGID_CREATELISTENER)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    # Set timers
    start_time = time.time()
    timeout = start_time + gtimeout
    # Begin operations
    while time.time() < (timeout + (2*poll_interval)):
        time.sleep(poll_interval)
        create_registry = register_device(bigip)
        create_uris = set(create_registry.keys()) - set(start_registry.keys())
        assert create_uris == SEG_INDEPENDENT_LB_URIS | NOSEG_LISTENER_URIS
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    assert ERROR_MSG_TIMEOUT in open(logfilename).read()

    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    # check for the expected number of calls to each rpc
    all_list = []
    for rpc_call in rpc.get_all_loadbalancers.call_args_list:
        all_list.append(str(rpc_call))
    assert len(all_list) > gtimeout+1
    one_list = []
    for rpc_call in rpc.get_service_by_loadbalancer_id.call_args_list:
        one_list.append(str(rpc_call))
    assert len(one_list) == gtimeout+1
    # check for the expected number of unique calls to each rpc
    assert len(set(all_list)) == 1
    assert len(set(one_list)) == 1
    # check for the expected status transitions
    assert rpc.update_listener_status.call_args_list == [
        call(u'105a227a-cdbf-4ce3-844c-9ebedec849e9', 'ACTIVE', 'OFFLINE'),
        call(u'105a227a-cdbf-4ce3-844c-9ebedec849e9', 'ERROR', 'OFFLINE')
    ]
    assert rpc.update_loadbalancer_status.call_args_list == [
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'OFFLINE'),
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'OFFLINE'),
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ERROR', 'OFFLINE')
    ]


def test_nosegid_to_segid(setup_l2adjacent_test, bigip):
    def create_swing_mock_rpc_plugin():
        # set up mock to return segid after 3 polling attempts
        mock_rpc_plugin = mock.MagicMock(name='swing_mock_rpc_plugin')
        mock_rpc_plugin.get_port_by_name.return_value = [
            {'fixed_ips': [{'ip_address': '10.2.2.134'}]}
        ]
        no_lb = []
        one_lb = [{'lb_id': '50c5d54a-5a9e-4a80-9e74-8400a461a077'}]
        mock_rpc_plugin.get_all_loadbalancers.side_effect = [
            no_lb, no_lb, no_lb, no_lb,
            one_lb, one_lb, one_lb, one_lb, one_lb, one_lb, one_lb, one_lb
        ]
        miss = deepcopy(NOSEGID_CREATELISTENER)
        miss['loadbalancer']['provisioning_status'] = "ACTIVE"
        hit = deepcopy(SEGID_CREATELISTENER)
        hit['loadbalancer']['provisioning_status'] = "ACTIVE"
        mock_rpc_plugin.get_service_by_loadbalancer_id.side_effect = [
            miss, deepcopy(miss), deepcopy(miss),
            hit, deepcopy(hit), deepcopy(hit), deepcopy(hit), deepcopy(hit),
            deepcopy(hit), deepcopy(hit), deepcopy(hit), deepcopy(hit)
        ]
        return mock_rpc_plugin
    # Configure
    icontroldriver, start_registry = handle_init_registry(
        bigip, FEATURE_ON, create_swing_mock_rpc_plugin)
    gtimeout = icontroldriver.conf.f5_network_segment_gross_timeout
    # Begin operations
    service = deepcopy(NOSEGID_CREATELISTENER)
    logcall(setup_l2adjacent_test,
            icontroldriver._common_service_handler,
            service)
    # Before gtimeout
    time.sleep(gtimeout)
    create_registry = register_device(bigip)
    create_uris = set(create_registry.keys()) - set(start_registry.keys())

    rpc = icontroldriver.plugin_rpc
    print(rpc.method_calls)
    # check for the expected number of calls to each rpc
    all_list = []
    for rpc_call in rpc.get_all_loadbalancers.call_args_list:
        all_list.append(str(rpc_call))
    assert len(all_list) > gtimeout
    one_list = []
    for rpc_call in rpc.get_service_by_loadbalancer_id.call_args_list:
        one_list.append(str(rpc_call))
    assert len(one_list) > gtimeout
    # check for the expected number of unique calls to each rpc
    assert len(set(all_list)) == 1
    assert len(set(one_list)) == 1
    assert create_uris == (SEG_INDEPENDENT_LB_URIS |
                           SEG_DEPENDENT_LB_URIS |
                           SEG_LISTENER_URIS)
    logfilename = setup_l2adjacent_test.baseFilename
    assert ERROR_MSG_TIMEOUT not in open(logfilename).read()
    assert ERROR_MSG_VXLAN_TUN not in open(logfilename).read()
    assert ERROR_MSG_MISCONFIG not in open(logfilename).read()
    # check that the last status update takes the object online
    assert list(rpc.update_loadbalancer_status.call_args_list)[-1] == (
        call(u'50c5d54a-5a9e-4a80-9e74-8400a461a077', 'ACTIVE', 'ONLINE')
    )
    assert rpc.update_listener_status.call_args_list[-1] == (
        call(u'105a227a-cdbf-4ce3-844c-9ebedec849e9', 'ACTIVE', 'ONLINE')
    )
