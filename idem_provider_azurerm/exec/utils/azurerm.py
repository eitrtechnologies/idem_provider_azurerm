# -*- coding: utf-8 -*-
'''
Azure Resource Manager (ARM) Utilities

.. versionadded:: 1.0.0

:maintainer: <devops@eitr.tech>
:maturity: new
:depends:
    * `azure <https://pypi.python.org/pypi/azure>`_ >= 2.0.0rc6
    * `azure-common <https://pypi.python.org/pypi/azure-common>`_ >= 1.1.4
    * `azure-mgmt <https://pypi.python.org/pypi/azure-mgmt>`_ >= 0.30.0rc6
    * `azure-mgmt-compute <https://pypi.python.org/pypi/azure-mgmt-compute>`_ >= 0.33.0
    * `azure-mgmt-network <https://pypi.python.org/pypi/azure-mgmt-network>`_ >= 0.30.0rc6
    * `azure-mgmt-resource <https://pypi.python.org/pypi/azure-mgmt-resource>`_ >= 0.30.0
    * `azure-mgmt-storage <https://pypi.python.org/pypi/azure-mgmt-storage>`_ >= 0.30.0rc6
    * `azure-mgmt-web <https://pypi.python.org/pypi/azure-mgmt-web>`_ >= 0.30.0rc6
    * `azure-storage <https://pypi.python.org/pypi/azure-storage>`_ >= 0.32.0
    * `msrestazure <https://pypi.python.org/pypi/msrestazure>`_ >= 0.4.21
:platform: linux

'''
# Import Python libs
from __future__ import absolute_import, print_function, unicode_literals
from operator import itemgetter
import importlib
import logging
import six
import sys
import os

try:
    from six.moves import range as six_range
except ImportError:
    six_range = range

# Import third party libs
try:
    from azure.common.credentials import (
        UserPassCredentials,
        ServicePrincipalCredentials,
    )
    from msrestazure.azure_cloud import (
        MetadataEndpointError,
        get_cloud_from_metadata_endpoint,
    )
    from msrestazure.azure_exceptions import CloudError
    HAS_AZURE = True
except ImportError:
    HAS_AZURE = False

try:
    from azure.identity import (
        DefaultAzureCredential,
        KnownAuthorities,
    )
    HAS_AZURE_ID = True
except ImportError:
    HAS_AZURE_ID = False

log = logging.getLogger(__name__)


async def determine_auth(hub, resource=None, **kwargs):
    '''
    Acquire Azure RM Credentials (mgmt modules)
    '''
    service_principal_creds_kwargs = ['client_id', 'secret', 'tenant']
    user_pass_creds_kwargs = ['username', 'password']

    cred_kwargs = {}

    if resource:
        cred_kwargs.update({'resource': resource})

    try:
        if kwargs.get('cloud_environment') and kwargs.get('cloud_environment').startswith('http'):
            cloud_env = get_cloud_from_metadata_endpoint(kwargs['cloud_environment'])
        else:
            cloud_env_module = importlib.import_module('msrestazure.azure_cloud')
            cloud_env = getattr(cloud_env_module, kwargs.get('cloud_environment', 'AZURE_PUBLIC_CLOUD'))
    except (AttributeError, ImportError, MetadataEndpointError):
        raise sys.exit('The Azure cloud environment {0} is not available.'.format(kwargs['cloud_environment']))

    if set(service_principal_creds_kwargs).issubset(kwargs):
        if not (kwargs['client_id'] and kwargs['secret'] and kwargs['tenant']):
            raise Exception(
                'The client_id, secret, and tenant parameters must all be '
                'populated if using service principals.'
            )
        else:
            credentials = ServicePrincipalCredentials(kwargs['client_id'],
                                                      kwargs['secret'],
                                                      tenant=kwargs['tenant'],
                                                      cloud_environment=cloud_env,
                                                      **cred_kwargs)
    elif set(user_pass_creds_kwargs).issubset(kwargs):
        if not (kwargs['username'] and kwargs['password']):
            raise Exception(
                'The username and password parameters must both be '
                'populated if using username/password authentication.'
            )
        else:
            credentials = UserPassCredentials(kwargs['username'],
                                              kwargs['password'],
                                              cloud_environment=cloud_env,
                                              **cred_kwargs)
    else:
        raise Exception(
            'Unable to determine credentials. '
            'A subscription_id with username and password, '
            'or client_id, secret, and tenant or a profile with the '
            'required parameters populated'
        )

    if 'subscription_id' not in kwargs:
        raise Exception(
            'A subscription_id must be specified'
        )

    subscription_id = str(kwargs['subscription_id'])

    return credentials, subscription_id, cloud_env


async def get_client(hub, client_type, **kwargs):
    '''
    Dynamically load the selected client and return a management client object
    '''
    client_map = {'compute': 'ComputeManagement',
                  'authorization': 'AuthorizationManagement',
                  'dns': 'DnsManagement',
                  'storage': 'StorageManagement',
                  'managementlock': 'ManagementLock',
                  'monitor': 'MonitorManagement',
                  'network': 'NetworkManagement',
                  'policy': 'Policy',
                  'resource': 'ResourceManagement',
                  'subscription': 'Subscription',
                  'web': 'WebSiteManagement',
                  'keyvault': 'KeyVaultManagement',
                  'redis': 'RedisManagement'}

    if client_type not in client_map:
        raise Exception(
            'The Azure ARM client_type {0} specified can not be found.'.format(
                client_type)
        )

    map_value = client_map[client_type]

    if client_type in ['policy', 'subscription']:
        module_name = 'resource'
    elif client_type in ['managementlock']:
        module_name = 'resource.locks'
    else:
        module_name = client_type

    try:
        client_module = importlib.import_module('azure.mgmt.'+module_name)
        # pylint: disable=invalid-name
        Client = getattr(client_module,
                         '{0}Client'.format(map_value))
    except ImportError:
        raise sys.exit(
                  'The azure {0} client is not available.'.format(client_type)
        )

    credentials, subscription_id, cloud_env = await hub.exec.utils.azurerm.determine_auth(**kwargs)

    if client_type == 'subscription':
        client = Client(
            credentials=credentials,
            base_url=cloud_env.endpoints.resource_manager,
        )
    else:
        client = Client(
            credentials=credentials,
            subscription_id=subscription_id,
            base_url=cloud_env.endpoints.resource_manager,
        )

    client.config.add_user_agent('Salt/{0}'.format('SOMEVERSIONHERE'))

    return client


async def log_cloud_error(hub, client, message, **kwargs):
    '''
    Log an azurearm cloud error exception
    '''
    try:
        cloud_logger = getattr(log, kwargs.get('azurearm_log_level'))
    except (AttributeError, TypeError):
        cloud_logger = getattr(log, 'error')

    cloud_logger(
         'An AzureARM %s CloudError has occurred: %s',
         client.capitalize(),
         message
    )

    return


async def paged_object_to_list(hub, paged_object):
    '''
    Extract all pages within a paged object as a list of dictionaries
    '''
    paged_return = []
    while True:
        try:
            page = next(paged_object)
            paged_return.append(page.as_dict())
        except CloudError:
            raise
        except StopIteration:
            break

    return paged_return


async def create_object_model(hub, module_name, object_name, **kwargs):
    '''
    Assemble an object from incoming parameters.
    '''
    object_kwargs = {}

    try:
        model_module = importlib.import_module('azure.mgmt.{0}.models'.format(module_name))
        # pylint: disable=invalid-name
        Model = getattr(model_module, object_name)
    except ImportError:
        raise sys.exit(
            'The {0} model in the {1} Azure module is not available.'.format(object_name, module_name)
        )

    if '_attribute_map' in dir(Model):
        for attr, items in Model._attribute_map.items():
            param = kwargs.get(attr)
            if param is not None:
                if items['type'][0].isupper() and isinstance(param, dict):
                    object_kwargs[attr] = await create_object_model(hub, module_name, items['type'], **param)
                elif items['type'][0] == '{' and isinstance(param, dict):
                    object_kwargs[attr] = param
                elif items['type'][0] == '[' and isinstance(param, list):
                    obj_list = []
                    for list_item in param:
                        if items['type'][1].isupper() and isinstance(list_item, dict):
                            obj_list.append(
                                await create_object_model(
                                    hub,
                                    module_name,
                                    items['type'][items['type'].index('[')+1:items['type'].rindex(']')],
                                    **list_item
                                )
                            )
                        elif items['type'][1] == '{' and isinstance(list_item, dict):
                            obj_list.append(list_item)
                        elif not items['type'][1].isupper() and items['type'][1] != '{':
                            obj_list.append(list_item)
                    object_kwargs[attr] = obj_list
                else:
                    object_kwargs[attr] = param

    # wrap calls to this function to catch TypeError exceptions
    return Model(**object_kwargs)


async def compare_list_of_dicts(hub, old, new, convert_id_to_name=None):
    '''
    Compare lists of dictionaries representing Azure objects. Only keys found in the "new" dictionaries are compared to
    the "old" dictionaries, since getting Azure objects from the API returns some read-only data which should not be
    used in the comparison. A list of parameter names can be passed in order to compare a bare object name to a full
    Azure ID path for brevity. If string types are found in values, comparison is case insensitive. Return comment
    should be used to trigger exit from the calling function.
    '''
    ret = {}

    if not convert_id_to_name:
        convert_id_to_name = []

    if not isinstance(new, list):
        ret['comment'] = 'must be provided as a list of dictionaries!'
        return ret

    if len(new) != len(old):
        ret['changes'] = {
            'old': old,
            'new': new
        }
        return ret

    try:
        local_configs, remote_configs = [sorted(config, key=itemgetter('name')) for config in (new, old)]
    except TypeError:
        ret['comment'] = 'configurations must be provided as a list of dictionaries!'
        return ret
    except KeyError:
        ret['comment'] = 'configuration dictionaries must contain the "name" key!'
        return ret

    for idx in six_range(0, len(local_configs)):
        for key in local_configs[idx]:
            local_val = local_configs[idx][key]
            if key in convert_id_to_name:
                remote_val = remote_configs[idx].get(key, {}).get('id', '').split('/')[-1]
            else:
                remote_val = remote_configs[idx].get(key)
                if isinstance(local_val, six.string_types):
                    local_val = local_val.lower()
                if isinstance(remote_val, six.string_types):
                    remote_val = remote_val.lower()
            if local_val != remote_val:
                ret['changes'] = {
                    'old': remote_configs,
                    'new': local_configs
                }
                return ret

    return ret


async def get_identity_credentials(hub, **kwargs):
    '''
    Acquire Azure RM Credentials from the identity provider (not for mgmt)

    This is accessible on the hub so clients out in the code can use it. Non-management clients
    can't be consolidated neatly here.

    We basically set environment variables based upon incoming parameters and then pass off to
    the DefaultAzureCredential object to correctly parse those environment variables. See the
    `Microsoft Docs on EnvironmentCredential <https://aka.ms/azsdk-python-identity-default-cred-ref>`_
    for more information.
    '''
    kwarg_map = {
        'tenant': 'AZURE_TENANT_ID',
        'client_id': 'AZURE_CLIENT_ID',
        'secret': 'AZURE_CLIENT_SECRET',
        'client_certificate_path': 'AZURE_CLIENT_CERTIFICATE_PATH',
        'username': 'AZURE_USERNAME',
        'password': 'AZURE_PASSWORD',
    }

    for kw in kwarg_map:
        if kwargs.get(kw):
            os.environ[kwarg_map[kw]] = kwargs[kw]

    try:
        if kwargs.get('cloud_environment') and kwargs.get('cloud_environment').startswith('http'):
            authority = kwargs['cloud_environment']
        else:
            authority = getattr(KnownAuthorities, kwargs.get('cloud_environment', 'AZURE_PUBLIC_CLOUD'))
        log.debug('AUTHORITY: %s', authority)
    except AttributeError as exc:
        log.error('Unknown authority presented for "cloud_environment": %s', exc)
        authority = KnownAuthorities.AZURE_PUBLIC_CLOUD

    credential = DefaultAzureCredential(authority=authority)

    return credential
