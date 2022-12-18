import json
import re

import config
from functools import cache
from rdflib import Literal, Graph, URIRef
from rdflib.namespace import Namespace, RDF, RDFS, OWL
import requests_cache
import sys
import yaml

from homeassistant.const import SERVICE_TOGGLE


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def mkname(name):
    return name.replace(" ", "_").replace("/", "_")


# In config: hass_url = "http://dehvl.local:8123/api/template"

session = requests_cache.CachedSession('my_cache')
session.headers = {'Content-type': 'application/json', 'Authorization': 'Bearer ' + config.hass_token}


def getYAML(query):
    http_data = {'template': '{{ '+query+' }}'}
    j_response = session.post(config.hass_url, json=http_data)
    if j_response.status_code == 200:
        return yaml.safe_load(j_response.text)
    else:
        eprint(f"JSON request failed: " + str(j_response.text))
        exit(1)


def getTextQuery(query):
    http_data = {'template': '{{ '+query+' }}'}
    j_response = session.post(config.hass_url, json=http_data)
    if j_response.status_code == 200:
        return j_response.text
    else:
        eprint(f"JSON request failed: " + str(j_response.text))
        exit(1)


def getDevices():
    return getYAML('states | map(attribute="entity_id")|map("device_id") | unique | reject("eq",None) | list')


def getDeviceEntities(device):
    return getYAML('device_entities("'+device+'")')


def getDeviceAttr(device, attr):
    return getYAML('device_attr("'+device+'","'+attr+'")')


def getStateAttr(e, attr):
    return getYAML(f'state_attr("{e}", "{attr}")')


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

    for d in getDevices():
        # https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/device_registry.py
        # TODO: table-based conversion, manufacturer -> hasManufacturer,
        #  maybe with lambdas for transformation?
        # TODO: Can we do this in a single HTTP-request/Jinja-template? Looks like {{ states.domain.entity.attributes }}
        #  should do the trick for `state_attr`.
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
            area = MINE[mkname(d_area)]
            g.add((area, RDF.type, S4BLDG['BuildingSpace']))
            g.add((area, S4BLDG['contains'], d_g))
        # END Area

        es = getDeviceEntities(d)

        if len(es) == 0:
            eprint(f"WARN: Device {name} does not have any entities?!")
        # elif len(es) == 1:
        #     # Only one device, let's special-case
        #     eprint(f"WARN: Device {name} does only have a single entity {es[0]}.")
        #     continue  # TODO
        else:
            # Create sub-devices
            for e in es:
                print(f"Handling {e}:")
                # Now let's find out the class:
                assert e.count('.') == 1
                (domain, e_name) = e.split('.')

                # Let's ignore those as spam for now.
                # Note that we don't seem to see the underlying radio-properties RSSI, LQI
                # that HA is hiding explicitly in the UI.
                if e_name.endswith("_identify"):
                    continue

                # Experimental section:
                # e_friendly_name = getYAML(f'state_attr("{e}", "friendly_name")')
                # END

                attrs = getAttributes(e)
                device_class = attrs['device_class'] if 'device_class' in attrs else None
                e_d = MINE[mkname(e_name)]
                if domain not in class_to_saref:
                    c = SAREF['Device']
                else:
                    c = class_to_saref[domain]
                    if c == SAREF['Sensor']:  # XXX?
                        # Special-casing (business rule):
                        if device_class == "temperature":
                            c = SAREF["TemperatureSensor"]
                            assert attrs['state_class'] == "SensorStateClass.MEASUREMENT", attrs
                        elif device_class == "humidity":
                            c = HASS['HumiditySensor']
                            assert attrs['state_class'] == "SensorStateClass.MEASUREMENT", attrs
                        elif device_class == "energy":
                            c = SAREF['Meter']
                            assert attrs['state_class'] == "SensorStateClass.TOTAL_INCREASING", attrs
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
                    g.add((d_g, SAREF['consistsOf'], e_d))

                    # Let's be careful what is MINE and what is in HASS below.
                    if domain == "switch":
                        # e_function = MINE[mkname(e_name)+"_function"]  # TODO: name?
                        # g.add((e_function, RDF.type, SAREF['OnOffFunction']))
                        # g.add((e_d, SAREF['hasFunction'], e_function))
                        # Tedious -- use Svcs-table?
                        serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, "_toggle", 'ServiceToggle')
                        serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, "_turnOn", 'ServiceTurnOn')
                        serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, "_turnOff", 'ServiceTurnOff')
                    elif domain == "button":
                        serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, "_press", 'ServicePress')
                    elif domain == "climate":
                        # TODO: get from Svcs-table
                        serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, "_turnOn", 'ServiceTurnOn')
                        serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, "_turnOff", 'ServiceTurnOff')
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
                            if device_class == "temperature":
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

    f_out = open("/Users/vs/ha.ttl", "w")
    print(g.serialize(format='turtle'), file=f_out)
    print(g.serialize(format='turtle'))
    exit(0)


def serviceOffer(HASS, MINE, SAREF, e_d, e_name, g, suffix, svc_name):
    e_service_inst = MINE[mkname(e_name) + suffix]
    g.add((e_service_inst, RDF.type, HASS[svc_name]))
    g.add((e_d, SAREF['offers'], e_service_inst))


@cache
def getAttributes(e):
    # Experimental
    # Not directly YAML due to embedded serialized objects:
    # - state_class
    # - 'hvac_modes': [<HVACMode.OFF: 'off'>,
    # - 'system_mode': '[<SystemMode.Heat: 4>]/heat'   <----- Quoted! Messes with regex a bit.
    #   https://github.com/home-assistant/core/blob/master/homeassistant/components/zha/climate.py#L166
    # - <Occupancy.Occupied: 1>
    # - <NumberMode.AUTO: 'auto'>
    #
    attrs = getTextQuery(f'states.{e}.attributes')
    # print(attrs)
    pat_re = compileMogrifier()  # Cacheable...does it help?
    a2 = attrs
    state_class_s = pat_re.search(a2)
    while state_class_s is not None:
        a2 = state_class_s.group(1).join([a2[:state_class_s.start()], a2[state_class_s.end():]])
        state_class_s = pat_re.search(a2)
    return yaml.safe_load(a2)


@cache
def compileMogrifier():
    # Not safe for quoted <>. Note "non-greedy" `+?`.
    return re.compile(r'<(.+?): \'?\w+?\'?>')


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

    # Inject Service-classes:
    svcs = {'ServiceToggle': ('Service', ['Light', 'Switch'])
            # This is weird: SAREF has SwitchOnService -- only:
            , 'ServiceTurnOn': ('SwitchOnService', ['Climate', 'Light', 'Switch'])
            , 'ServiceTurnOff': ('Service', ['Climate', 'Light', 'Switch'])
            , 'ServicePress': ('Service', ['Button'])}
    for hass_svc, val in svcs.items():
        saref_svc, _ = val
        g.add((HASS[hass_svc], RDFS.subClassOf, SAREF[saref_svc]))


    # Let's patch SAREF a bit with our extensions:
    g.add((HASS['HumiditySensor'], RDFS.subClassOf, SAREF['Sensor']))
    g.add((HASS['Button'], RDFS.subClassOf, SAREF['Actuator']))  # ?
    # END

    return svcs, g, MINE, HASS, SAREF, S4BLDG


if __name__ == "__main__":
    main()
