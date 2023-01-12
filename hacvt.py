import json
import re

import homeassistant.const

import config
import homeassistant.core as ha
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import SensorDeviceClass

from functools import cache
from rdflib import Literal, Graph, URIRef
from rdflib.namespace import Namespace, RDF, RDFS, OWL
import requests_cache
import sys
import yaml


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def mkname(name):
    if isinstance(name, (int, float)):
        name = str(name)
    return name.replace(" ", "_").replace("/", "_")


def mkEntityURI(MINE, entity_id):
    _, e_name = ha.split_entity_id(entity_id)
    return MINE["entity/"+mkname(e_name)]


def mkLocationURI(MINE, l_name):
    return MINE["location/"+mkname(l_name)]


# In config: hass_url = "http://dehvl.local:8123/api/template"

session = requests_cache.CachedSession('my_cache')
session.headers = {'Content-type': 'application/json', 'Authorization': 'Bearer ' + config.hass_token}


def getYAML(query):
    http_data = {'template': '{{ '+query+' }}'}
    j_response = session.post(config.hass_url+"template", json=http_data)
    assert j_response.status_code == 200, f"YAML request failed: " + str(j_response.text)
    return yaml.safe_load(j_response.text)


def getTextQuery(query):
    # Unused
    http_data = {'template': '{{ '+query+' }}'}
    j_response = session.post(config.hass_url+"template", json=http_data)
    assert j_response.status_code == 200, f"JSON request failed: " + str(j_response.text)
    return j_response.text


def getDevices():
    return getYAML('states | map(attribute="entity_id")|map("device_id") | unique | reject("eq",None) | list')


def getDeviceEntities(device):
    return getYAML('device_entities("'+device+'")')


def getDeviceAttr(device, attr):
    return getYAML('device_attr("'+device+'","'+attr+'")')


# TODOs
# - escape "/" in names!

def main():
    Svcs, g, MINE, HASS, SAREF, S4BLDG = setupSAREF()
    # Load known types:
    master = Graph()
    master.parse("https://saref.etsi.org/core/v3.1.1/saref.ttl")

    class_to_saref = {
        # https://github.com/home-assistant/core/blob/master/homeassistant/const.py
        "climate": SAREF["HVAC"]
        , "button": HASS["Button"]
        , "sensor": SAREF["Sensor"]
        , "binary_sensor": SAREF["Sensor"]
        , "light": HASS["Light"]
        , "switch": SAREF["Switch"]
        , "device_tracker": SAREF["Sensor"]
        , "device": SAREF["Device"]  # of course...
        # We skip those for now -- are these maybe also just Sensors?
        , "select": None  # SERVICE_SELECT_OPTION
        , "number": None  # SERVICE_SET_VALUE
    }

    the_devices = getDevices()
    for d in the_devices:
        # https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/device_registry.py
        # TODO: table-based conversion, manufacturer -> hasManufacturer,
        #  maybe with lambdas for transformation?
        manufacturer = getDeviceAttr(d, 'manufacturer')
        name = getDeviceAttr(d, 'name')
        model = getDeviceAttr(d, 'model')
        name_by_user = getDeviceAttr(d, 'name_by_user')
        # O'sama denn hier? TODO.
        entry_type = getDeviceAttr(d, 'entry_type')
        if not entry_type == "None":
            eprint(f"INFO: Found {d} {name} as: {entry_type}")

        d_g = MINE[mkname(name if name_by_user == "None" else name_by_user)]
        g.add((d_g, RDF.type, SAREF['Device']))
        g.add((d_g, SAREF['hasManufacturer'], Literal(manufacturer)))
        g.add((d_g, SAREF['hasModel'], Literal(model)))

        # Handle 'Area' of devices. May be None.
        # TODO: Entities can override this individually.
        d_area = getYAML(f'area_id("{d}")')
        if not d_area == "None":  # Careful, string!
            area = mkLocationURI(MINE, d_area)
            g.add((area, RDF.type, S4BLDG['BuildingSpace']))
            g.add((area, S4BLDG['contains'], d_g))
        # END Area

        # Handle `via_device` if present.
        via = getDeviceAttr(d, 'via_device')
        if via != "None":
            other = None
            # Matches construction of d_g above:
            d2_name = getDeviceAttr(via, 'name')
            d2_name_by_user = getDeviceAttr(via, 'name_by_user')
            other = MINE[mkname(d2_name if d2_name_by_user == "None" else d2_name_by_user)]
            eprint(f"INFO: Found via({d},{via})")
            g.add(d_g, HASS['via_device'], other)
        # END via_device

        es = getDeviceEntities(d)

        if len(es) == 0:
            eprint(f"WARN: Device {name} does not have any entities?!")
        # elif len(es) == 1:
        #     # Only one device, let's special-case
        #     eprint(f"WARN: Device {name} does only have a single entity {es[0]}.")
        #     continue  # TODO
        else:
            # Create sub-devices

            # Let's ignore those as spam for now.
            # Note that we don't seem to see the underlying radio-properties RSSI, LQI
            # that HA is hiding explicitly in the UI.
            def checkName(n):
                assert n.count('.') == 1
                (_domain, e_name) = ha.split_entity_id(n)
                return not e_name.endswith("_identify")

            for e in filter(checkName, es):
                eprint(f"Handling {e}:")
                e_d = handle_entity(HASS, MINE, SAREF, class_to_saref, e, g, master)
                # Derived entities and helpers are their own devices:
                g.add((d_g, SAREF['consistsOf'], e_d))

        for e in getEntitiesWODevice():
            # These have an empty inverse of `consistsOf`
            handle_entity(HASS, MINE, SAREF, class_to_saref, e['entity_id'], g, master)

        ha_automation = HASS['Automation']
        for a_id, a in getAutomations().items():
            _, a_name = ha.split_entity_id(a_id)
            a_o = MINE[mkname(a_name)]
            g.add((a_o, RDF.type, ha_automation))  # Apparently we also subclass Device somewhere else
            g.add((a_o, HASS['friendly_name'], Literal(a['friendly_name'])))
            # {'id': '1672829613487', 'alias': 'Floorheating BACK', 'description': '', 'trigger': [{'platform': 'time', 'at': 'input_datetime.floorheating_on'}], 'condition': [], 'action': [{'service': 'climate.set_temperature', 'data': {'temperature': 17}, 'target': {'device_id': 'ec5cb183f030a83754c6f402af08420f'}}], 'mode': 'single'}
            result = session.get(f"{config.hass_url}config/automation/config/{a['id']}")
            a_config = result.json()
            i = 0
            for an_action in a_config['action']:
                # Convert back to its type:
                the_action = cv.determine_script_action(an_action)
                # TODO: assert HASS[the_action] already exists since we should have the schema.
                o_action = HASS[the_action]
                o_action_instance = MINE[mkname(a_name)+"_"+str(i)]
                g.add((o_action_instance, RDF.type, o_action))
                g.add((a_o, HASS['consistsOf'], o_action_instance))  # TODO: create multiplicity in schema
                i = i+1
                if the_action == cv.SCRIPT_ACTION_CALL_SERVICE:
                    service_id = an_action['service']
                    assert not isinstance(service_id, list)
                    if 'entity_id' in an_action['target']:
                        # That may be a list!
                        if isinstance(an_action['target']['entity_id'], list):
                            es = an_action['target']['entity_id']
                        else:
                            es = [an_action['target']['entity_id']]
                        for e in es:
                            # This is a little bit tricky where we diverge from HA's modelling:
                            #  We have a concrete instance already which corresponds to this particular pair of `service, target`.
                            _, t_name = ha.split_entity_id(e)
                            _, service_name = ha.split_entity_id(service_id)
                            target_entity = MINE[mkname(t_name)+"_"+service_name]
                            g.add((o_action_instance, HASS['target'], target_entity))
                    else:
                        eprint(an_action)
                        exit(1)
                elif the_action == cv.SCRIPT_ACTION_DEVICE_AUTOMATION:
                    _, e_name = ha.split_entity_id(an_action['entity_id'])
                    # Inventing 'target' here, there isn't much in Python?
                    g.add((o_action_instance, HASS['target'], MINE[mkname(e_name)+"_"+the_action]))
                elif the_action == cv.SCRIPT_ACTION_DELAY:
                    eprint(f"WARN: Skipping {the_action}: {str(an_action)}")
                    pass
                else:
                    eprint(the_action+ ":" +str(an_action))
                    exit(2)
                # TODO: populate schema by action type
                # `action`s are governed by: https://github.com/home-assistant/core/blob/31a787558fd312331b55e5c2c4b33341fc3601fc/homeassistant/helpers/script.py#L270
                # After that it's following the `_SCHEMA`

    # Print Turtle output both to file and console:
    f_out = open("/Users/vs/ha.ttl", "w")
    print(g.serialize(format='turtle'), file=f_out)
    print(g.serialize(format='turtle'))
    exit(0)


def mkServiceURI(MINE, SAREF, service_id):
    _, service_name = ha.split_entity_id(service_id)
    if service_name == homeassistant.const.SERVICE_TURN_ON:  # dupe TODO
        e_service_instance = SAREF["SwitchOnService"]
    else:
        e_service_instance = MINE["service/"+mkname(service_name) + "_service"]
    return e_service_instance


def handle_entity(HASS, MINE, SAREF, class_to_saref, e, g, master):
    assert e.count('.') == 1
    (domain, e_name) = ha.split_entity_id(e)
    # Experimental section:
    # e_friendly_name = getYAML(f'state_attr("{e}", "friendly_name")')
    # END
    attrs = getAttributes(e)
    device_class = attrs['device_class'] if 'device_class' in attrs else None
    # TODO: Probably creates overlapping names!
    e_d = mkEntityURI(MINE, e)
    if domain not in class_to_saref:
        c = SAREF['Device']
    else:
        c = class_to_saref[domain]
        if c == SAREF['Sensor']:  # XXX?
            # Special-casing (business rule):
            if device_class == "temperature":
                c = SAREF["TemperatureSensor"]
                assert attrs['state_class'] == "measurement", attrs
            elif device_class == "humidity":
                c = HASS['HumiditySensor']
                assert attrs['state_class'] == "measurement", attrs
            elif device_class == "energy":
                c = SAREF['Meter']
                # TODO -- probably we shouldn't be asserting those things.
                # assert attrs['state_class'] == "total_increasing", attrs
            else:
                # Spam:
                if device_class is not None:
                    eprint(f"WARN: Not handling class {device_class} (yet).")
            # END
    if c is None:
        eprint(f"WARN: Skipping {e} (no mapping for domain {domain}).")
    else:
        # https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/entity_registry.py

        g.add((e_d, RDF.type, c))

        # Look up services this domain should have, and create them for this entity.
        if domain in getServices():
            for service in getServices()[domain]:
                # Silly mapping, also see below.
                if service == homeassistant.const.SERVICE_TURN_ON:
                    s_class = SAREF["SwitchOnService"]
                else:
                    s_class = HASS[service]
                # TODO: constructed name is ... meh...
                serviceOffer(MINE, SAREF, e_d, e_name, g, "_" + service, s_class)

        # Let's be careful what is MINE and what is in HASS below.
        if domain == homeassistant.const.Platform.SWITCH:  # TODO: more of those.
            # e_function = MINE[mkname(e_name)+"_function"]  # TODO: name?
            # g.add((e_function, RDF.type, SAREF['OnOffFunction']))
            # g.add((e_d, SAREF['hasFunction'], e_function))
            pass
        elif domain == "button":
            pass
        elif domain == "climate":
            # Business rule: https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/__init__.py#L214
            # Are we delivering temperature readings, e.g. an HVAC?
            q = attrs['current_temperature'] if 'current_temperature' in attrs else None
            if q is not None:
                g.add((e_d, RDF.type, SAREF['TemperatureSensor']))
            q = attrs['current_humidity'] if 'current_humidity' in attrs else None
            if q is not None:
                g.add((e_d, RDF.type, HASS['HumiditySensor']))
            # END
        elif domain == "binary_sensor" or domain == "sensor":  # Handle both types in one for now.
            # https://github.com/home-assistant/core/blob/dev/homeassistant/components/binary_sensor/__init__.py
            if device_class is not None:
                # Patch lower-case names:
                q = device_class.title()
                # Let's look it up in the SAREF "master-list":
                q_o = hasEntity(master, SAREF, q)
                if q_o is None:
                    eprint(f"INFO: Creating {q}.")
                    q_o = HASS[q]
                    # Create Property...
                    g.add((q_o, RDFS.subClassOf, SAREF['Property']))
                    # ...and instance:
                q_prop = MINE[f"{q}_prop"]
                g.add((q_prop, RDF.type, q_o))
                g.add((e_d, SAREF['measuresProperty'], q_prop))
            #
            q = attrs['unit_of_measurement'] if 'unit_of_measurement' in attrs else None
            if q is not None:
                if device_class == SensorDeviceClass.TEMPERATURE:
                    unit = SAREF['TemperatureUnit']
                elif device_class == "current":
                    unit = SAREF['PowerUnit']
                elif device_class == "power":
                    unit = SAREF['PowerUnit']
                elif device_class == "energy":
                    unit = SAREF['EnergyUnit']
                elif device_class == "pressure":
                    unit = SAREF['PressureUnit']
                else:  # Not built-in.
                    if q == "mbar":  # WIP
                        assert False, device_class
                    unit = HASS[mkname(q)]
                    g.add((unit, RDFS.subClassOf, SAREF['UnitOfMeasure']))
                g.add((MINE[mkname(q)], RDF.type, unit))
        elif domain == "light":
            # brightness = 0 results in "None", silly, so we can't distinguish
            # "off" from "doesn't exist"?
            q = attrs['brightness'] if 'brightness' in attrs else None
            if q is not None:
                brightness_prop = MINE['Brightness_prop']
                g.add((brightness_prop, RDF.type, HASS['Brightness']))
                # TODO: XXX Nope, we're not measuring, we're setting!?
                g.add((e_d, SAREF['measuresProperty'], brightness_prop))
    return e_d


def serviceOffer(MINE, SAREF, e_d, e_name, g, suffix, svc_obj):
    e_service_inst = MINE["service/"+mkname(e_name) + suffix]
    g.add((e_service_inst, RDF.type, svc_obj))
    g.add((e_d, SAREF['offers'], e_service_inst))


@cache
def getAttributes(e):
    # TODO: could bounce through cached getStates() now.
    result = session.get(f"{config.hass_url}states/{e}")
    j = json.loads(result.text)
    return j['attributes'] if 'attributes' in j else []


@cache
def getStates():
    result = session.get(f"{config.hass_url}states")
    return result.json()


def getEntitiesWODevice():
    for k in getStates():
        if 'device_id' not in k:
            yield k


def getAutomations():
    result = session.get(f"{config.hass_url}states")
    out = {}
    for k in json.loads(result.text):
        if k['entity_id'].startswith("automation."):
            out[k['entity_id']] = k['attributes']
    return out


@cache
def getServices():
    result = session.get(f"{config.hass_url}services")
    assert result.status_code == 200, (result.status_code, result.text)
    out = {}
    for k in json.loads(result.text):
        out[k['domain']] = k['services']
    return out


def mkServiceToDomainTable():
    hass_svcs = {}
    for component_name, svc_services in getServices().items():
        for s in svc_services:
            if s in hass_svcs:
                t = hass_svcs[s]
                t.add(component_name)
                hass_svcs[s] = t
            else:
                hass_svcs[s] = set([component_name])
    return hass_svcs


@cache
def hasEntity(master, SAREF, q):
    # TODO: At least we're caching now...but we could precompute a dictionary.
    for s, _, _ in master.triples((None, RDFS.subClassOf, SAREF['Property'])):
        if s.endswith("/" + q):
            return SAREF[q]
    return None


def setupSAREF():
    g = Graph(bind_namespaces="core")
    SAREF = Namespace("https://saref.etsi.org/core/")
    S4BLDG = Namespace("https://saref.etsi.org/saref4bldg/")
    MINE = Namespace("http://my.name.spc/")
    HASS = Namespace("http://home-assistant.io/")
    g.bind("saref", SAREF)
    g.bind("owl", OWL)
    g.bind("s4bldg", S4BLDG)
    g.bind("mine", MINE)
    g.bind("hass", HASS)
    saref_import = URIRef("http://my.name.spc/")  # Check! MINE?
    g.add((saref_import, RDF.type, OWL.Ontology))
    g.add((saref_import, OWL.imports, URIRef(str(SAREF))))
    g.add((saref_import, OWL.imports, URIRef(str(S4BLDG))))

    # Experimental
    # Manual entities, e.g. from "lights":

    # 4.3.3: "a dimmer lamp is a device that is of type saref:Actuator"
    g.add((HASS['Light'], RDFS.subClassOf, SAREF['Actuator']))
    # TODO: light maybe_has brightness?
    g.add((HASS['Brightness'], RDFS.subClassOf, SAREF['Property']))

    # Inject Service-classes
    # Note that using /api/services only gives you the services of your instance. Here, we want to create
    #  the metamodel/profile for HA, so we use the CSV generated from a git-checkout.
    # We still need a static map to SAREF.

    hass_svcs = mkServiceToDomainTable()
    for s, domains in hass_svcs.items():
        # This is weird: SAREF has SwitchOnService -- only:
        if s == homeassistant.const.SERVICE_TURN_ON:
            s = "SwitchOnService"
        g.add((HASS[s], RDFS.subClassOf, SAREF['Service']))
        # WIP -- do we want to inject ALL HASS classes below the corresponding SAREF devices?
        # for d in domains:
        #    print(s,d)
        #    g.add((HASS[s], MINE['provided'], HASS[d]))

    # Let's patch SAREF a bit with our extensions:
    g.add((HASS['HumiditySensor'], RDFS.subClassOf, SAREF['Sensor']))
    g.add((HASS['Button'], RDFS.subClassOf, SAREF['Actuator']))  # ?
    # END

    # BEGIN SCHEMA metadata, reflection on
    #  https://github.com/home-assistant/core/blob/9f7fd8956f22bd873d14ae89460cdffe6ef6f85d/homeassistant/helpers/config_validation.py#L1641
    ha_action = HASS['Action']
    for k, v in cv.ACTION_TYPE_SCHEMAS.items():
        g.add((HASS[k], RDFS.subClassOf, ha_action))
    # END

    # TODO: Export HASS schema as separate file and import in model, instead of having it in the graph. (#5)
    # TODO: Should probably be in a class...
    return hass_svcs, g, MINE, HASS, SAREF, S4BLDG


if __name__ == "__main__":
    main()
