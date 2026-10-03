"""Port and controller evidence must explain the declared restart transition."""
from sre_bench.fixtures import SimCluster, load_fixture


def port(pod):
    return next(int(e['value']) for e in pod['env'] if e['name'] == 'DB_PORT')


def test_restart_repairs_stale_application_configuration_not_database():
    sim = SimCluster(load_fixture('sre_crashloop_resolution'))
    app, db = sim.state['pods']
    desired = int(sim.state['configmaps'][0]['data']['DB_PORT'])
    service = sim.state['services'][0]['spec']['ports'][0]
    assert desired == service['port'] == service['targetPort'] == 5432
    assert f'listening on {desired}' in db['logs']
    assert port(app) != desired
    assert f":{port(app)}..." in app['logs']
    before_db = dict(db)
    sim.call('restart_deployment', namespace='production', name='api-server', confirmed=True)
    assert sim.verification_passed is None
    after = sim.call('describe_pod', namespace='production', name=app['name'])
    assert after['phase'] == 'Running'
    assert all(container['ready'] for container in after['containers'])
    assert port(after) == desired
    assert f':{desired}...' in sim.state['pods'][0]['logs']
    assert sim.state['pods'][1] == before_db
    assert sim.verification_passed is True


def test_restart_target_has_observed_controller_uid_chain():
    sim = SimCluster(load_fixture('sre_crashloop_resolution'))
    obj = sim.call('describe_pod', name='api-server', namespace='production')
    for kind in ('ReplicaSet', 'Deployment'):
        owner = obj['metadata']['ownerReferences'][0]
        assert owner['controller'] is True and owner['kind'] == kind
        obj = sim.call('describe_resource', kind=kind, name=owner['name'], namespace='production')
        assert obj['metadata']['uid'] == owner['uid']
    assert obj['name'] == sim.remediation['args']['name']
    owner = sim.state['pods'][1]['metadata']['ownerReferences'][0]
    obj = sim.call('describe_resource', kind='StatefulSet', name=owner['name'], namespace='production')
    assert obj['metadata']['uid'] == owner['uid']


def test_unconfirmed_and_wrong_target_do_not_heal():
    for args in ({'name': 'api-server'}, {'name': 'other', 'confirmed': True}):
        sim = SimCluster(load_fixture('sre_crashloop_resolution'))
        sim.call('restart_deployment', namespace='production', **args)
        sim.call('describe_pod', name='api-server', namespace='production')
        assert sim.state['pods'][0]['phase'] == 'CrashLoopBackOff'
        assert sim.verification_passed is not True


def test_describe_resource_requires_real_kind_namespace_and_name():
    sim = SimCluster(load_fixture('sre_crashloop_resolution'))
    for args in (
        {'kind': 'Deployment', 'namespace': 'other', 'name': 'api-server'},
        {'kind': 'StatefulSet', 'namespace': 'production', 'name': 'api-server'},
        {'kind': 'Deployment', 'namespace': 'production', 'name': 'missing'},
    ):
        assert sim.call('describe_resource', **args)['error']['code'] == 404
    result = sim.call('describe_resource', kind='deployments', namespace='production', name='api-server')
    result['metadata']['uid'] = 'changed'
    assert sim.state['deployments'][0]['metadata']['uid'] == 'deployment-api-server'


def test_describe_resource_preserves_explicit_canned_response():
    sim = SimCluster({'responses': {'describe_resource': {'legacy': True}}})
    assert sim.call('describe_resource', kind='Deployment', name='anything') == {'legacy': True}


def test_declared_healing_does_not_connect_to_a_non_listening_database_port():
    import re

    sim = SimCluster(load_fixture('sre_crashloop_resolution'))
    sim.call('restart_deployment', namespace='production', name='api-server', confirmed=True)
    application = sim.call('get_pod_logs', name='api-server', namespace='production')['logs']
    database = sim.call('get_pod_logs', name='db-postgres-0', namespace='production')['logs']
    destination = re.search(r'db-service\.production\.svc:(\d+)', application).group(1)
    listener = re.search(r'listening on (\d+)', database).group(1)
    assert destination == listener, 'restart must not invent a successful connection to an unchanged wrong-port DB'
