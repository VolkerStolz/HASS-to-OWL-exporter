import config
from functools import cache
from rdflib import Literal, Graph, URIRef
from rdflib.namespace import Namespace, RDF, RDFS, OWL
import requests_cache
import sys
import yaml


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
    g, MINE, HASS, SAREF, S4BLDG = setupSAREF()
    # Load known types:
    master = Graph()
    master.parse("https://saref.etsi.org/core/v3.1.1/saref.ttl")

    class_to_saref = {
        "climate": SAREF["HVAC"]
        , "button": SAREF["Actuator"]
        , "sensor": SAREF["Sensor"]
        , "binary_sensor": SAREF["Sensor"]
        , "light": HASS["Light"]
        , "switch": SAREF["Switch"]
        , "device_tracker": SAREF["Sensor"]
        , "device": SAREF["Device"]  # of course...
        # We skip those for now -- are these maybe also just Sensors?
        , "select": None
        , "number": None
    }

    for d in getDevices():
        # https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/device_registry.py
        # TODO: table-based conversion, manufacturer -> hasManufacturer,
        #  maybe with lambdas for transformation?
        # TODO: Can we do this in a single HTTP-request/Jinja-template?
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
        elif len(es) == 1:
            # Only one device, let's special-case
            eprint(f"WARN: Device {name} does only have a single entity {es[0]}.")
            continue  # TODO
        else:
            # Create sub-devices
            for e in es:
                print(f"Handling {e}:")
                # Now let's find out the class:
                assert e.count('.') == 1
                (domain, e_name) = e.split('.')

                # Let's ignore those as spam for now:
                if e_name.endswith("_identify"):
                    continue

                # e_friendly_name = getYAML(f'state_attr("{e}", "friendly_name")')
                e_d = MINE[mkname(e_name)]
                if domain not in class_to_saref:
                    c = SAREF['Device']
                else:
                    c = class_to_saref[domain]
                    if c == SAREF['Sensor']:  # XXX?
                        # Special-casing (business rule):
                        device_class = getStateAttr(e, "device_class")
                        if device_class == "temperature":
                            c = SAREF["TemperatureSensor"]
                        elif device_class == "humidity":
                            c = HASS['HumiditySensor']
                        else:
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
                        e_function = MINE[mkname(e_name)]  # TODO: name?
                        g.add((e_function, RDF.type, SAREF['OnOffFunction']))
                        g.add((e_d, SAREF['hasFunction'], e_function))
                    elif domain == "climate":
                        # Business rule: https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/__init__.py#L214
                        # Are we delivering temperature readings, e.g. an HVAC?
                        q = getStateAttr(e, "current_temperature")
                        if q != "None":
                            g.add((e_d, RDF.type, SAREF['TemperatureSensor']))
                        q = getStateAttr(e, "current_humidity")
                        if q != "None":
                            g.add((e_d, RDF.type, HASS['HumiditySensor']))
                        # END
                    elif domain == "binary_sensor" or domain == "sensor":  # Handle both types in one for now.
                        # https://github.com/home-assistant/core/blob/dev/homeassistant/components/binary_sensor/__init__.py
                        q = getStateAttr(e, 'device_class')
                        if q != "None":
                            # Patch lower-case names:
                            q = q.title()
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
                    elif domain == "light":
                        # brightness = 0 results in "None", silly, so we can't distinguish
                        # "off" from "doesn't exist"?
                        q = getStateAttr(e, 'brightness')
                        if q != "None":
                            brightness_prop = MINE['Brightness_prop']
                            g.add((brightness_prop, RDF.type, HASS['Brightness']))
                            # TODO: XXX Nope, we're not measuring, we're setting!
                            g.add((e_d, SAREF['measuresProperty'], brightness_prop))


    f_out = open("/Users/vs/ha.ttl", "w")
    print(g.serialize(format='turtle'), file=f_out)
    print(g.serialize(format='turtle'))
    exit(0)


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
    saref_import = URIRef("http://my.name.spc/")  # Check!
    g.add((saref_import, RDF.type, OWL.Ontology))
    g.add((saref_import, OWL.imports, URIRef(str(SAREF))))
    g.add((saref_import, OWL.imports, URIRef(str(S4BLDG))))

    # Experimental
    # Manual entities, e.g. from "lights":
    light = HASS['Light']
    g.add((light, RDFS.subClassOf, SAREF['Appliance']))  # ?
    # TODO: light maybe_has brightness?
    g.add((HASS['Brightness'], RDFS.subClassOf, SAREF['Property']))

    # Let's patch SAREF a bit with our extensions:
    g.add((HASS['HumiditySensor'], RDFS.subClassOf, SAREF['Sensor']))
    # END

    return g, MINE, HASS, SAREF, S4BLDG


if __name__ == "__main__":
    main()

exit(1)

for (id, d) in my_devs.items():
    d_g = URIRef("http://my.name.spc/" + mkname(d['name']))
    d_cl = "Device"  # to be overriden below
    cl = "Switch"

    # Only assign one class? [TODO]
    d_cl = "Sensor"
    for s in d['sensors']:
        # update class?
        # XXX Bad idea is a Sensor provides multiple values?!
        if s['original_device_class'] == "temperature":
            d_cl = "TemperatureSensor"
        e_name = s['name']
        # TODO: same safety-net for switches?
        if e_name is None:
            e_name = s['original_name']
            if e_name is None:
                e_name = s['entity_id']
        assert e_name is not None, str(s)
        e_m = URIRef("http://my.name.spc/measure/" + mkname(e_name) + "_" + s['id'][0:3])  # TODO: id?
        g.add((e_m, RDF.type, SAREF['Measurement']))
        g.add((d_g, SAREF['makesMeasurement'], e_m))

        e_prop = URIRef("http://my.name.spc/property/" + mkname(e_name))
        # I want my multi-level modelling back...
        unit = None
        if s['original_device_class'] == "temperature":
            unit = URIRef("http://my.name.spc/unit/" + mkname(s['unit_of_measurement']))
            # TODO: I think we're adding dupes here?
            g.add((unit, RDF.type, SAREF['TemperatureUnit']))
            cl = SAREF['Temperature']
        elif s['original_device_class'] == "current":
            unit = URIRef("http://my.name.spc/unit/" + mkname(s['unit_of_measurement']))
            g.add((unit, RDF.type, SAREF['PowerUnit']))
            cl = SAREF['Power']
        elif s['original_device_class'] == "humidity":
            unit = URIRef("http://my.name.spc/unit/" + mkname(s['unit_of_measurement']))
            # LOL, in SAREF, Power has PowerUnit, but Humidity doesn't have a unit...
            g.add((unit, RDF.type, SAREF['UnitOfMeasure']))
            cl = SAREF['Humidity']
        else:
            cl = SAREF['Property']
        # assert cl subclassof Property
        g.add((e_prop, RDF.type, cl))
        if unit is not None:
            g.add((e_m, SAREF['isMeasuredIn'], unit))
        g.add((d_g, SAREF['measuresProperty'], e_prop))
        g.add((e_m, SAREF['relatesToProperty'], e_prop))  # inverse inferred in Protégé
