import os
from pathlib import Path
from base64 import b64encode

import yaml
from charmhelpers.core import hookenv
from charms import layer
from charms.reactive import hook, clear_flag, set_flag, when, when_any, when_not, endpoint_from_name


@hook('upgrade-charm')
def upgrade_charm():
    clear_flag('charm.started')


@when('charm.started')
def charm_ready():
    layer.status.active('')


@when_any('layer.docker-resource.oci-image.changed', 'config.changed')
def update_image():
    clear_flag('charm.started')


@when('layer.docker-resource.oci-image.available', 'endpoint.minio.joined')
@when_not('charm.started')
def start_charm():
    if not hookenv.is_leader():
        hookenv.log("This unit is not a leader.")
        return False

    layer.status.maintenance('configuring container')

    try:
        minio = endpoint_from_name('minio').mailman3()[0]
    except IndexError:
        layer.status.waiting('Waiting for minio relation.')
        return False

    if not all(minio.values()):
        layer.status.waiting("Waiting for full minio relation.")
        return False

    image_info = layer.docker_resource.get_info('oci-image')

    clusterworkflowtemplates = yaml.safe_load(Path("files/argoproj.io_clusterworkflowtemplates.yaml").read_text())
    cronworkflows = yaml.safe_load(Path("files/argoproj.io_cronworkflows.yaml").read_text())
    workflows = yaml.safe_load(Path("files/argoproj.io_workflows.yaml").read_text())
    workfloweventbindings = yaml.safe_load(Path("files/argoproj.io_workfloweventbindings.yaml").read_text())
    workflowtemplates = yaml.safe_load(Path("files/argoproj.io_workflowtemplates.yaml").read_text())

    layer.caas_base.pod_spec_set(
        spec={
            'version': 2,
            'serviceAccount': {
                'global': True,
                'rules': [
                    {
                        'apiGroups': [''],
                        'resources': ['pods', 'pods/exec'],
                        'verbs': ['create', 'get', 'list', 'watch', 'update', 'patch', 'delete'],
                    },
                    {
                        'apiGroups': [''],
                        'resources': ['configmaps'],
                        'verbs': ['get', 'watch', 'list'],
                    },
                    {
                        'apiGroups': [''],
                        'resources': ['persistentvolumeclaims'],
                        'verbs': ['create', 'delete', 'get'],
                    },
                    {
                        'apiGroups': [''],
                        'resources': ['serviceaccounts'],
                        'verbs': ['get', 'list'],
                    },
                    {
                        'apiGroups': [''],
                        'resources': ['events'],
                        'verbs': ['create', 'patch'],
                    },
                    {
                        'apiGroups': ['argoproj.io'],
                        'resources': ['workflows', 'workflows/finalizers'],
                        'verbs': ['get', 'list', 'watch', 'update', 'patch', 'delete', 'create'],
                    },
                    {
                        'apiGroups': ['argoproj.io'],
                        'resources': ['cronworkflows', 'cronworkflows/finalizers'],
                        'verbs': ['get', 'list', 'watch', 'update', 'patch', 'delete'],
                    },
                    {
                        'apiGroups': ['argoproj.io'],
                        'resources': ['workflowtemplates', 'workflowtemplates/finalizers',
                                      'clusterworkflowtemplates', 
                                      'clusterworkflowtemplates/finalizers'],
                        'verbs': ['get', 'list', 'watch'],
                    },
                    {
                        'apiGroups': ['policy'],
                        'resources': ['poddisruptionbudgets'],
                        'verbs': ['create', 'delete', 'get'],
                    },
                ],
            },
            'containers': [
                {
                    'name': 'argo-controller',
                    'command': ['workflow-controller'],
                    'args': [
                        '--configmap',
                        'argo-controller-configmap-config',
                        '--executor-image',
                        'argoproj/argoexec:v2.12.8',
                    ],
                    'imageDetails': {
                        'imagePath': image_info.registry_path,
                        'username': image_info.username,
                        'password': image_info.password,
                    },
                    'config': {'ARGO_NAMESPACE': os.environ['JUJU_MODEL_NAME']},
                    'files': [
                        {
                            'name': 'configmap',
                            'mountPath': '/config-map.yaml',
                            'files': {
                                'config': yaml.dump(
                                    {
                                        'executorImage': 'argoproj/argoexec:v2.12.8',
                                        'containerRuntimeExecutor': hookenv.config('executor'),
                                        'kubeletInsecure': hookenv.config('kubelet-insecure'),
                                        'artifactRepository': {
                                            's3': {
                                                'bucket': hookenv.config('bucket'),
                                                'keyPrefix': hookenv.config('key-prefix'),
                                                'endpoint': f'{minio["ip"]}:{minio["port"]}',
                                                'insecure': True,
                                                'accessKeySecret': {
                                                    'name': 'mlpipeline-minio-artifact',
                                                    'key': 'accesskey',
                                                },
                                                'secretKeySecret': {
                                                    'name': 'mlpipeline-minio-artifact',
                                                    'key': 'secretkey',
                                                },
                                            }
                                        },
                                    }
                                )
                            },
                        }
                    ],
                }
            ],
        },
        k8s_resources={
            'kubernetesResources': {
                'customResourceDefinitions': {
                    clusterworkflowtemplates['metadata']['name']: clusterworkflowtemplates['spec'],
                    cronworkflows['metadata']['name']: cronworkflows['spec'],
                    workflows['metadata']['name']: workflows['spec'],
                    workfloweventbindings['metadata']['name']: workfloweventbindings['spec'],
                    workflowtemplates['metadata']['name']: workflowtemplates['spec'],
                },
                'secrets': [
                    {
                        'name': 'mlpipeline-minio-artifact',
                        'type': 'Opaque',
                        'data': {
                            'accesskey': b64encode(
                                minio['user'].encode('utf-8')
                            ),
                            'secretkey': b64encode(
                                minio['password'].encode('utf-8')
                            ),
                        },
                    }
                ],
            }
        },
    )

    layer.status.maintenance('creating container')
    set_flag('charm.started')
