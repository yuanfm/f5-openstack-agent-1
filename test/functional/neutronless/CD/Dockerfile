From test_base:latest

COPY ./f5-openstack-test/ /root/devenv/f5-openstack-test/
RUN pip install --upgrade /root/devenv/f5-openstack-test
COPY ./f5-openstack-agent/ /root/devenv/f5-openstack-agent/
RUN pip install --upgrade /root/devenv/f5-openstack-agent
COPY ./f5-common-python/ /root/devenv/f5-common-python/
RUN pip install --upgrade /root/devenv/f5-common-python/
VOLUME /root/devenv /root/devenv
RUN py.test --tb=no -v --symbols /root/devenv/f5-openstack-agent/devtools/common_service_handler_env.json -v /root/devenv/f5-openstack-agent/test/functional/neutronless/CD/test_loadbalancer.py
