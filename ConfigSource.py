import argparse
import functools
import json
import logging
import os
import socket
import ssl
import sys
import urllib.parse

import requests
from forcediphttpsadapter.adapters import ForcedIPHTTPSAdapter
from functools import cache
import requests_cache
import websocket
import yaml

logger = logging.getLogger(__name__)


class HAException(Exception):
    pass


class ConfigSource:
    # session = requests_cache.CachedSession('my_cache')
    session = requests.Session()
    mount_ip = None  # XXX Doesn't really belong in here now that we have Flask.

    def __init__(self, url, token):
        self.hass_url = url
        self.token = token
        self.session.headers = {'Content-type': 'application/json',
                                'Authorization': 'Bearer ' + token,
                                'User-Agent': 'HOWL-exporter/0.1 vs+howl@foldr.org'
                                }

    def getYAML(self, query):
        http_data = {'template': '{{ ' + query + ' }}'}
        j_response = self.session.post(self.hass_url + "template", json=http_data)
        if j_response.status_code == 401:
            # Let's quickly check if the token is valid via GET:
            self.getStates()
            raise HAException("Your token does not seem to have admin privileges that the tool needs to execute some " \
                          "queries via templates.\n Please obtain an admin-token and try again.")
        assert j_response.status_code == 200, f"YAML request failed: " + str(j_response.text)
        return yaml.safe_load(j_response.text)

    def getYAMLText(self, query) -> str:
        http_data = {'template': '{{ ' + query + ' }}'}
        j_response = self.session.post(self.hass_url + "template", json=http_data)
        assert j_response.status_code == 200, f"YAML request failed: " + str(j_response.text)
        return j_response.text

    @cache
    def getDevices(self):
        q = {'type': "config/device_registry/list", 'id': self.ws_counter}
        self.ws_counter += 1
        self.ws.send(json.dumps(q))
        q_result = json.loads(self.ws.recv())
        assert q_result['success'], q_result
        r_list = q_result['result']
        q_list = []
        for r in r_list:
            print(r)
            q_list.append(r['id'])

        # TODO: remove after enough testing
        t = self.getYAML('states | map(attribute="entity_id")|map("device_id") | unique | reject("eq",None) | list')
        failed_a = []
        failed_b = []
        for x in t:
            if not (x in q_list):
                failed_a.append(x)
        for x in q_list:
            if not(x in t):
                failed_b.append(x)

        # Sanity check for now:
        assert len(failed_a) == 0, f"Not in ws: {failed_a}\nNot in REST: {failed_b}"
        return q_list

    def _ws_connect(self, certificate=None):
        ws = websocket.WebSocket()
        ws_url = "ws" + self.hass_url[4:] + "websocket"
        header = ['User-Agent: HOWL-exporter/0.1 vs+howl@foldr.org']
        if self.mount_ip is not None:
            urlp = urllib.parse.urlparse(ws_url)
            s = socket.create_connection((self.mount_ip, urlp.port))
            if urlp.scheme == "wss":
                if certificate is not None:
                    if self.session.verify is None:
                        ctx = ssl.create_default_context()
                        ctx.verify_mode = ssl.CERT_NONE
                    else:
                        ctx = ssl.create_default_context(cafile=self.session.cert)
                else:
                    ctx = ssl.create_default_context()
                s = ctx.wrap_socket(s, server_hostname=urlp.hostname)
            ws.connect(ws_url, socket=s, header=header)
        else:
            if not (certificate is None):
                if self.session.verify is None:
                    sslopt = {"cert_reqs": ssl.CERT_NONE}
                else:
                    sslopt = {"ca_cert": self.session.cert}
            else:
                sslopt = {}
            # Create a new connection because `connect` won't actually `sslopt` otherwise.
            ws = websocket.WebSocket(sslopt=sslopt)
            logger.info(f'Connecting to {ws_url}...')
            ws.connect(ws_url, header=header)
        welcome_msg = ws.recv()  # Consume hello-msg
        assert welcome_msg.startswith('{"type":"auth_required"'), welcome_msg
        q = {'type': "auth", 'access_token': self.token}
        ws.send(json.dumps(q))
        auth_result = json.loads(ws.recv())
        assert auth_result['type'] == "auth_ok", auth_result
        return ws

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


class CLISource(ConfigSource):

    def __init__(self, parser: argparse.ArgumentParser):
        parser.add_argument('url',
                            help='Full path to API, e.g. https://homeassistant.local:8123/api/.')
        parser.add_argument('token', metavar='TOKENVAR',
                            help='Name of environment variable where you keep your long-lived access token. NOT THE LITERAL TOKEN!')
        parser.add_argument('-m', '--mount', metavar='192.0.2.1',
                            help='Use ForcedIPHTTPSAdapter to override IP for URL; useful on internal IPs.')
        parser.add_argument('-c', '--certificate', metavar='ca.crt',
                            help='Path to a CA certificate to validate your https-connection if needed. The string'
                                 ' "None" will disable validation.')
        args = parser.parse_args()  # TODO: could be more modular in the future
        self.args = args  # We may need the results outside.
        token = os.getenv(args.token)
        if token is None:
            print(f"Aborting: the environment variable for the token that you specified on the command line does not"
                  f" seem to be set!", file=sys.stderr)
            exit(1)
        super().__init__(args.url, token)
        # Set on-demand.
        if args.mount is not None:
            self.session.mount(args.url, ForcedIPHTTPSAdapter(dest_ip=args.mount))
            self.mount_ip = args.mount
        else:
            self.mount_ip = None
        if args.certificate is not None:
            if args.certificate == "None":
                self.session.verify = None
            else:
                self.session.verify = args.certificate
