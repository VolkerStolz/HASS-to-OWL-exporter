import json
from functools import cache

import config

import requests_cache
import yaml


class ConfigSource:
    pass


class RESTSource(ConfigSource):
    # In config.py: hass_url = "http://dehvl.local:8123/api/"
    session = requests_cache.CachedSession('my_cache')
    session.headers = {'Content-type': 'application/json', 'Authorization': 'Bearer ' + config.hass_token}

    def getYAML(self, query):
        http_data = {'template': '{{ '+query+' }}'}
        j_response = self.session.post(config.hass_url+"template", json=http_data)
        assert j_response.status_code == 200, f"YAML request failed: " + str(j_response.text)
        return yaml.safe_load(j_response.text)

    def getTextQuery(self, query):
        # Unused
        http_data = {'template': '{{ ' + query + ' }}'}
        j_response = self.session.post(config.hass_url + "template", json=http_data)
        assert j_response.status_code == 200, f"JSON request failed: " + str(j_response.text)
        return j_response.text

    def getDevices(self):
        return self.getYAML('states | map(attribute="entity_id")|map("device_id") | unique | reject("eq",None) | list')

    def getDeviceEntities(self, device):
        return self.getYAML('device_entities("' + device + '")')

    @cache
    def getDeviceAttr(self, device, attr):
        return self.getYAML('device_attr("' + device + '","' + attr + '")')

    @cache
    def getDeviceId(self, entity):
        return self.getYAML(f'device_id("{entity}")')

    @cache
    def getAttributes(self, e):
        # TODO: could bounce through cached getStates() now.
        result = self.session.get(f"{config.hass_url}states/{e}")
        j = json.loads(result.text)
        return j['attributes'] if 'attributes' in j else []

    @cache
    def getStates(self):
        result = self.session.get(f"{config.hass_url}states")
        return result.json()

    @cache
    def getServices(self):
        result = self.session.get(f"{config.hass_url}services")
        assert result.status_code == 200, (result.status_code, result.text)
        out = {}
        for k in json.loads(result.text):
            out[k['domain']] = k['services']
        return out

    @cache
    def getAutomationConfig(self, automation_id):
        result = self.session.get(f"{config.hass_url}config/automation/config/{automation_id}")
        return result.json()
