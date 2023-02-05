import argparse
import json
import os
import sys

import requests
from forcediphttpsadapter.adapters import ForcedIPHTTPSAdapter
from functools import cache
import requests_cache
import yaml


class ConfigSource:
    # TODO: Life is too short to design interfaces...
    pass


class RESTSource(ConfigSource):
    # session = requests_cache.CachedSession('my_cache')
    session = requests.Session()

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument('url',
                            help='Full path to API, e.g. https://homeassistant.local:8123/api/.')
        parser.add_argument('token', metavar='TOKEN',
                            help='Name of environment variable where you keep your long-lived access token.')
        parser.add_argument('-m', '--mount', metavar='192.0.2.1',
                            help='Use ForcedIPHTTPSAdapter to override IP for URL.')
        parser.add_argument('-c', '--certificate', metavar='ca.crt',
                            help='Path a CA certificate to validate your https-connection if needed.')
        args = parser.parse_args()  # TODO: could be more modular in the future
        self.args = args  # We may need the results outside.
        token = os.getenv(args.token)
        if token is None:
            print(f"Aborting: the environment variable for the token that you specified on the command line does not"
                  f" seem to be set!", file=sys.stderr)
            exit(1)
        self.hass_url = args.url
        self.session.headers = {'Content-type': 'application/json',
                                'Authorization': 'Bearer ' + token,
                                'User-Agent': 'HOWL-exporter/0.1 vs+howl@foldr.org'
                                }
        # Set on-demand.
        if args.mount is not None:
            self.session.mount(args.url, ForcedIPHTTPSAdapter(dest_ip=args.mount))
        if args.certificate is not None:
            self.session.verify = args.certificate

    def getYAML(self, query):
        http_data = {'template': '{{ ' + query + ' }}'}
        j_response = self.session.post(self.hass_url + "template", json=http_data)
        assert j_response.status_code == 200, f"YAML request failed: " + str(j_response.text)
        return yaml.safe_load(j_response.text)

    def getYAMLText(self, query) -> str:
        http_data = {'template': '{{ ' + query + ' }}'}
        j_response = self.session.post(self.hass_url + "template", json=http_data)
        assert j_response.status_code == 200, f"YAML request failed: " + str(j_response.text)
        return j_response.text

    def getDevices(self):
        return self.getYAML('states | map(attribute="entity_id")|map("device_id") | unique | reject("eq",None) | list')

    def getDeviceEntities(self, device):
        return self.getYAML('device_entities("' + device + '")')

    @cache
    def getDeviceAttr(self, device, attr) -> str:
        return self.getYAMLText('device_attr("' + device + '","' + attr + '")')

    @cache
    def getDeviceId(self, entity):
        return self.getYAML(f'device_id("{entity}")')

    @cache
    def getAttributes(self, e):
        # TODO: could bounce through cached getStates() now.
        result = self.session.get(f"{self.hass_url}states/{e}")
        j = json.loads(result.text)
        return j['attributes'] if 'attributes' in j else {}

    @cache
    def getStates(self):
        result = self.session.get(f"{self.hass_url}states")
        return result.json()

    @cache
    def getServices(self):
        result = self.session.get(f"{self.hass_url}services")
        assert result.status_code == 200, (result.status_code, result.text)
        out = {}
        for k in result.json():
            out[k['domain']] = k['services']
        return out

    @cache
    def getAutomationConfig(self, automation_id):
        result = self.session.get(f"{self.hass_url}config/automation/config/{automation_id}")
        assert result.status_code == 200, (result.status_code, result.text)
        return result.json()
