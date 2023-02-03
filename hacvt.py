import homeassistant.const as hc
import homeassistant.core as ha
from homeassistant.helpers.trigger import _PLATFORM_ALIASES
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components import (
    automation,
    binary_sensor,
    button,
    climate,
    device_automation,
    fan,
    light,
    mqtt,
    remote,
    sensor,
    sun,
    switch,
    zone,
)
import homeassistant.components.button.device_action
import homeassistant.components.climate.device_action
import homeassistant.components.fan.device_action
import homeassistant.components.light.device_action
import homeassistant.components.mqtt.device_trigger
import homeassistant.components.switch.device_action
import homeassistant.components.zone.trigger

# Import some constants for later use: obviously this ain't gonna scale!
from homeassistant.components.binary_sensor.device_trigger import CONF_MOTION, CONF_NO_MOTION
# from homeassistant.components.deconz.device_trigger import CONF_SHORT_PRESS, CONF_LONG_PRESS

from functools import cache
import logging
from rdflib import Literal, Graph, URIRef
from rdflib.namespace import Namespace, RDF, RDFS, OWL
from typing import Optional

from ConfigSource import RESTSource

logging.basicConfig(level='INFO', format='%(levelname)s: %(message)s')

cs = RESTSource()  # Use REST API. Configure `config.py`!

# BEGIN Privacy settings:
p_counter = 0  # Used to enumerate privatized entity names
# Adjust the following to preserve device/entity names. We first take the default platforms,
# then some common ones. Set to privacy_filter to None to disable. The code-layout here should make it
# easy to comment out/in individual items.
privacy_filter = set(list(hc.Platform))
# Identifier for devices in your system:
privacy_filter.add("device")
# Useful components that we usually want exported that are not part of home-assistant's core:
for p in {"climate", "input_datetime", "sun", "time"}:
    privacy_filter.add(p)
# Common things that you may want to adjust to keep them private and that are not part of the based platforms:
privacy_filter.discard("device_tracker")  # Anonymize your mobile devices if you use the app.
# privacy_filter.add("person")              # Export our accounts in home-assistant.
privacy_filter.add("area")  # Export your self-defined area-names...
privacy_filter.add("zone")  # ... and zones.
# Final switch to export everything unfiltered, overriding anything above:
privacy_filter = None

# Log what we're doing:
msg = "ALL" if privacy_filter is None else str(privacy_filter)
logging.info(f"Preserving entities: {msg}")


# END Privacy settings


def mkname(name):
    if isinstance(name, (int, float)):
        name = str(name)
    return name.replace(" ", "_").replace("/", "_")


def mkEntityURI(MINE, entity_id) -> tuple[URIRef, str]:
    global p_counter
    # TODO: Not sure what we want to assume here for uniqueness...probably want to keep domain.name instead of
    #  discarding it? Note that adding may happen somewhere else, as does setting a `friendly name` if it exists.
    try:
        e_platform, e_name = ha.split_entity_id(entity_id)
        # we use a white-list in the privacy filter:
        if privacy_filter is not None and e_platform not in privacy_filter:
            e_name = "entity_" + str(p_counter)
            p_counter = p_counter + 1
        return MINE["entity/" + e_platform + "_" + mkname(e_name)], e_name
    except:
        logging.fatal(f"Can't process URI {entity_id}.")
        raise


def mkLocationURI(MINE, name):
    global p_counter
    if privacy_filter is not None and "area" not in privacy_filter:
        name = "entity_" + str(p_counter)
        p_counter = p_counter + 1
    return MINE["area/" + mkname(name)]


# TODOs
# - escape "/" in names!

def main():
    g = Graph(bind_namespaces="core")
    MINE, HASS, SAREF, S4BLDG, class_to_saref = setupSAREF(g, importsOnly=False)
    # Save metamodel:
    f_out = open("homeassistantcore.rdf", "w")
    print(g.serialize(format='application/rdf+xml'), file=f_out)
    # ... and get us a fresh graph:
    g = Graph(bind_namespaces="core")
    MINE, HASS, SAREF, S4BLDG, _ = setupSAREF(g, importsOnly=True)
    g.add((URIRef(str(MINE)), OWL.imports, URIRef(str(HASS))))

    # Load known types:
    master = Graph()
    master.parse("https://saref.etsi.org/core/v3.1.1/saref.ttl")

    the_devices = cs.getDevices()
    for d in the_devices:
        # https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/device_registry.py
        # For these we don't have constants.
        # TODO: table-based conversion, manufacturer -> hasManufacturer,
        #  maybe with lambdas for transformation?
        manufacturer = cs.getDeviceAttr(d, hc.ATTR_MANUFACTURER)
        name = cs.getDeviceAttr(d, hc.ATTR_NAME)
        model = cs.getDeviceAttr(d, hc.ATTR_MODEL)
        # O'sama denn hier? TODO.
        entry_type = cs.getDeviceAttr(d, 'entry_type')
        if not entry_type == "None":
            logging.info(f"Found {d} {name} as: {entry_type}")

        d_g = mkDevice(MINE, d)
        # TODO: The following is of course already a design-decision. We just create a container w/o particular type.
        # TODO: Discuss with Fernando & Eduard.
        g.add((d_g, RDF.type, SAREF['Device']))
        g.add((d_g, SAREF['hasManufacturer'], Literal(manufacturer)))
        g.add((d_g, SAREF['hasModel'], Literal(model)))

        # Handle 'Area' of devices. May be None.
        # TODO: Entities can override this individually.
        d_area = cs.getYAMLText(f'area_id("{d}")')
        area_name = cs.getYAMLText(f'area_name("{d}")')
        if not d_area == "None":  # Careful, string!
            area = mkLocationURI(MINE, d_area)
            g.add((area, RDF.type, S4BLDG['BuildingSpace']))
            g.add((area, S4BLDG['contains'], d_g))
            g.add((area, RDFS.label, Literal(area_name)))
        # END Area

        # Handle `via_device` if present.
        via = cs.getDeviceAttr(d, 'via_device')
        if via != "None":
            # Matches construction of d_g above:
            other = mkDevice(MINE, via)
            logging.info(f"Found via({d},{via})")
            g.add((d_g, HASS['via_device'], other))
        # END via_device

        es = cs.getDeviceEntities(d)

        if len(es) == 0:
            logging.info(f"Device {name} does not have any entities?!")
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
                return not (e_name.endswith("_identify") or e_name.endswith("_identifybutton"))

            for e in filter(checkName, es):
                e_d = handle_entity(HASS, MINE, SAREF, class_to_saref, d, e, g, master)
                if e_d is not None:
                    # Derived entities and helpers are their own devices:
                    g.add((d_g, SAREF['consistsOf'], e_d))

    for e in getEntitiesWODevice():
        # These have an empty inverse of `consistsOf`
        platform, name = ha.split_entity_id(e['entity_id'])
        # Not a constant?
        if platform == automation.const.DOMAIN:
            handleAutomation(master, HASS, MINE, e['attributes'], name, g)
        else:
            handle_entity(HASS, MINE, SAREF, class_to_saref, None, e['entity_id'], g, master)

    # Print Turtle output both to file and console:
    f_out = open("ha.ttl", "w")
    print(g.serialize(format='turtle'), file=f_out)
    print(g.serialize(format='turtle'))
    exit(0)


def mkDevice(MINE, device_id):
    global p_counter
    d2_name = cs.getDeviceAttr(device_id, 'name')
    d2_name_by_user = "None"
    if privacy_filter is not None and "device" not in privacy_filter:
        d2_name = "device_" + str(p_counter)
        p_counter = p_counter + 1
    else:
        d2_name_by_user = cs.getDeviceAttr(device_id, 'name_by_user')
    return MINE[mkname(d2_name if d2_name_by_user == "None" else d2_name_by_user)]


def handleAutomation(master, HASS, MINE, a, a_name, g):
    c_trigger = 0
    a_o = MINE["automation/" + mkname(a_name)]
    g.add((a_o, RDF.type, HASS['Automation']))
    if hc.ATTR_FRIENDLY_NAME in a:
        g.add((a_o, RDFS.label, Literal(a[hc.ATTR_FRIENDLY_NAME])))
    # {'id': '1672829613487', 'alias': 'Floorheating BACK', 'description': '', 'trigger': [{'platform': 'time', 'at': 'input_datetime.floorheating_on'}], 'condition': [], 'action': [{'service': 'climate.set_temperature', 'data': {'temperature': 17}, 'target': {'device_id': 'ec5cb183f030a83754c6f402af08420f'}}], 'mode': 'single'}
    if 'id' not in a:
        logging.warning(f"Skipping automation {a_name} because it doesn't have an id.")
        return  # Can't really proceed without a config here.
    a_config = cs.getAutomationConfig(a['id'])
    i = 0  # We'll number the container-elements
    for an_action in a_config['action']:
        # Convert back to its type:
        the_action = cv.determine_script_action(an_action)
        # This call seems to perform some lifting, e.g. in cases where a one-element list
        #  would be a single element in JSON, but the code would like to work with the list.
        # assert cv.script_action(an_action) == an_action, (an_action, cv.script_action(an_action))
        an_action = cv.script_action(an_action)
        # TODO: assert HASS[the_action] already exists since we should have the schema.
        # But no worky:
        # assert hasEntity(master, Namespace("http://home-assistant.io/action/"), 'Action', the_action), the_action
        o_action = HASS["action/" + the_action.title()]
        o_action_instance = MINE["action/" + mkname(a_name) + "_" + str(i)]
        g.add((o_action_instance, RDF.type, o_action))
        g.add((a_o, HASS['consistsOf'], o_action_instance))  # TODO: XXX create multiplicity+order in schema
        i = i + 1
        if the_action == cv.SCRIPT_ACTION_CALL_SERVICE:
            # TODO: use schema in Python...SERVICE_SCHEMA
            # The big problem is that there are tons of modules bringing their own stuff where
            #  HA essentially uses reflection at runtime to deal with them e.g. in the UI.
            # If we want a stable static schema, the only way forward would be to try and integrate
            #  INTO HA, and try to intercept schemata etc. when a plugin checks in on startup.
            # It's unclear to me if that would work, and would require deeper digging.
            #
            # The cv.* below is what is prescribed by HA, and maybe we don't have to dig deeper
            #  than `target`
            # TODO: Everythign here is optional...
            if cv.CONF_TARGET in an_action and cv.CONF_SERVICE in an_action:
                service_id = an_action[cv.CONF_SERVICE]
                target = an_action[cv.CONF_TARGET]
                process_target_schema(HASS, MINE, g, o_action_instance, service_id, target)

        elif the_action == cv.SCRIPT_ACTION_DEVICE_AUTOMATION:
            # Schema prescribes only `device_id` and `domain`.
            # https://www.home-assistant.io/integrations/device_automation/
            device = mkDevice(MINE, an_action[cv.CONF_DEVICE_ID])
            g.add((o_action_instance, HASS['device'], device))

            def button_action(config):
                e_id, _ = mkEntityURI(MINE, an_action[cv.CONF_ENTITY_ID])
                g.add((o_action_instance, HASS['press'], e_id))

            def climate_action(config):
                c = climate.device_action.ACTION_SCHEMA(config)
                # dispatch on ACTION_TYPE, assemble action based on entity/mode:
                _, e_name = ha.split_entity_id(c[hc.CONF_ENTITY_ID])
                name = mkname(e_name) + "_" + c[hc.CONF_TYPE]
                # TODO: should be intermediate object? Or subclassing?
                my_service = MINE["service/" + name.title()]
                g.add((o_action_instance, HASS['target'], my_service))
                # Modelling happening here:
                g.add((my_service, RDFS.subClassOf, HASS["Climate_Mode"]))
                if c[hc.CONF_TYPE] == "set_hvac_mode":
                    mode = c[climate.ATTR_HVAC_MODE]
                    # Sloppy, should be enum:
                    g.add((my_service, HASS['mode'], Literal(mode)))
                elif c[hc.CONF_TYPE] == "set_preset_mode":
                    mode = c[climate.ATTR_PRESET_MODE]
                    g.add((my_service, HASS['mode'], Literal(mode)))
                else:
                    logging.fatal(f"Action Oops: {c}.")
                pass

            def toggle_action(config):
                # Obs: we can't call the toggle_entity-schema validator, since the action
                #   may have contributed new things.
                c = config  # device_automation.toggle_entity.ACTION_SCHEMA(config)

                if c[hc.CONF_TYPE] in device_automation.toggle_entity.DEVICE_ACTION_TYPES:
                    _, e_name = ha.split_entity_id(c[cv.CONF_ENTITY_ID])
                    name = mkname(e_name) + "_" + c[hc.CONF_TYPE]
                    # assert hasEntity(master, Namespace("http://my.name.spc/service/"), 'Service', name) is not None, name
                    # TODO: Why "service", not "action"?
                    g.add((o_action_instance, HASS['target'], MINE["service/" + name]))
                else:
                    # Not for us here.
                    pass
                return c[cv.CONF_ENTITY_ID], c[hc.CONF_TYPE]

            def light_action(config):
                # "Inherits" from device_automation/toggle_entity
                e_id, type = toggle_action(config)
                opt_flash = config[light.device_action.ATTR_FLASH] if light.device_action in config else None
                opt_bright_pct = config[light.device_action.ATTR_BRIGHTNESS_PCT] if light.device_action in config else None
                # TODO: Can model here! It looks like that you can set DECREASE and still set PCT > 10 :-)
                if type == light.device_action.TYPE_BRIGHTNESS_DECREASE:
                    # default: -10
                    # TODO: Review with Fernando
                    # TODO: superclass + attributes
                    g.add((o_action_instance, RDFS.subClassOf, HASS['LIGHT_ACTION_CHANGE_BRIGHTNESS']))
                    g.add((o_action_instance, HASS['changeBrightness'], Literal(opt_bright_pct if opt_bright_pct is not None else -10)))
                elif type == light.device_action.TYPE_BRIGHTNESS_INCREASE:
                    # default: +10
                    g.add((o_action_instance, RDFS.subClassOf, HASS['LIGHT_ACTION_CHANGE_BRIGHTNESS']))
                    g.add((o_action_instance, HASS['changeBrightness'], Literal(opt_bright_pct if opt_bright_pct is not None else 10)))
                elif type == light.device_action.TYPE_FLASH:
                    # default = short according to source.
                    g.add((o_action_instance, RDFS.subClassOf, HASS['LIGHT_ACTION_FLASH']))
                    g.add((o_action_instance, HASS['flash'], Literal(opt_flash if opt_flash is not None else "short")))
                else:
                    logging.fatal(f"Unsupported type in: {config}")

            action_table = {
                hc.Platform.BINARY_SENSOR: None,
                hc.Platform.BUTTON: (button.device_action.ACTION_SCHEMA, button_action),
                hc.Platform.CLIMATE: (climate.device_action.ACTION_SCHEMA, climate_action),
                # hc.Platform.FAN: (fan.device_action.ACTION_SCHEMA, fan_action),
                hc.Platform.LIGHT: (light.device_action.ACTION_SCHEMA, light_action),
                hc.Platform.SENSOR: None,
                hc.Platform.SWITCH: (switch.device_action.ACTION_SCHEMA, toggle_action),  # Nothing more.
            }
            if an_action[cv.CONF_DOMAIN] not in action_table:
                logging.error(f"Skipping action domain {an_action[cv.CONF_DOMAIN]}.")
            else:
                schema, action = action_table[an_action[cv.CONF_DOMAIN]]
                if action is not None:
                    # And off we go!
                    action(schema(an_action))
        elif the_action == cv.SCRIPT_ACTION_DELAY:
            g.add((o_action_instance, HASS['delay'],  # sloppy
                   Literal(str(cv.time_period(an_action[cv.CONF_DELAY])))))
        else:
            logging.warning("Skipping action " + the_action + ":" + str(an_action))  # TODO
        # TODO: populate schema by action type
        # `action`s are governed by: https://github.com/home-assistant/core/blob/31a787558fd312331b55e5c2c4b33341fc3601fc/homeassistant/helpers/script.py#L270
        # After that it's following the `_SCHEMA`
    for a_trigger in a_config['trigger']:
        for t in cv.TRIGGER_SCHEMA(a_trigger):
            # Only `platform` is mandatory.
            c_trigger = c_trigger + 1
            if not any(x for x in hc.Platform if x.name == t[cv.CONF_PLATFORM]):
                # These are some built-ins:
                if not (t[cv.CONF_PLATFORM] in _PLATFORM_ALIASES["device_automation"]
                        or t[cv.CONF_PLATFORM] in _PLATFORM_ALIASES["homeassistant"]):
                    logging.info(f"Found custom platform `{t[cv.CONF_PLATFORM]}`")

            o_trigger = MINE["trigger/" + a_name + str(c_trigger)]

            e_id = t['entity_id'] if 'entity_id' in t else None
            if e_id is not None:
                # This can actually be a list.
                # TODO: isn't there a schema that should list this for us?
                if isinstance(e_id, list):
                    assert len(e_id) > 0
                    if len(e_id) > 1:
                        logging.critical("Found a trigger with multiple entities. Cowardly refusing to process anything but the first.")
                    e_id = e_id[0]
                trigger_entity, _ = mkEntityURI(MINE, e_id)
                g.add((o_trigger, HASS['trigger_entity'], trigger_entity))

            # Still a bit unclear here. An Action Trigger schema is very general,
            #  whereas the concrete schemas like binary_sensor/device_trigger.py prescribe more elements,
            #  most importantly `entity_id`
            if t[cv.CONF_PLATFORM] == "device":
                trigger_device = mkDevice(MINE, t['device_id'])
                g.add((o_trigger, HASS['device'], trigger_device))
                trigger_type = HASS["type/" + t['type']]  # TODO: static? May not be possible/effective,
                # ...since there's no global super-container? This must be INSIDE Homeassistant! #5
                # What I don't know is if the triggers etc. are installed even though you're not using the integration...
                g.add((trigger_type, RDFS.subClassOf, HASS["type/TriggerType"]))
                # Does this code below does scale? Of course we could enumerate all Sensor/BinarySensor types.
                if t[hc.CONF_TYPE] == CONF_MOTION or t[hc.CONF_TYPE] == CONF_NO_MOTION:
                    attrs = cs.getAttributes(e_id)
                    d_class = attrs['device_class'] if 'device_class' in attrs else None
                    # assert d_class == BinarySensorDeviceClass.MOTION, d_class  # Not all code seems to respect this?
                    # We've already added the entity, but also add the device:
                    # TODO: solves this a bit higher up in general?
                    g.add((o_trigger, HASS['trigger_device'], trigger_device))
                elif t[hc.CONF_TYPE] == "remote_button_short_press" or t['type'] == "remote_button_long_press":
                    # This is coming from deconz, and fat chance that we will be transcribing all of this by hand!
                    g.add((o_trigger, HASS['device'], trigger_device))
                else:
                    logging.warning(f"not handling trigger {t} yet.")
                    pass
                g.add((o_trigger, RDF.type, trigger_type))
            elif t[cv.CONF_PLATFORM] == zone.const.DOMAIN:
                e_id = t[hc.CONF_ENTITY_ID]  # already done
                e_zone = t[hc.CONF_ZONE]
                o_zone, _ = mkEntityURI(MINE, e_zone)
                e_event = t[hc.CONF_EVENT]
                trigger_type = HASS["type/ZoneTrigger"]
                g.add((o_trigger, RDF.type, trigger_type))
                mkDirectAttribute(HASS, hc.CONF_EVENT, g, o_trigger, t)
                g.add((o_trigger, HASS['zone'], o_zone))
            elif t[hc.CONF_PLATFORM] == "state":
                # TODO: from/to literals
                trigger_type = HASS["type/StateTrigger"]
                g.add((o_trigger, RDF.type, trigger_type))
            elif t[hc.CONF_PLATFORM] == "numeric_state":
                trigger_type = HASS["type/NumericStateTrigger"]
                g.add((o_trigger, RDF.type, trigger_type))
                #             vol.Required(CONF_PLATFORM): "numeric_state",
                #             vol.Required(CONF_ENTITY_ID): cv.entity_ids_or_uuids,
                #             vol.Optional(CONF_BELOW): cv.NUMERIC_STATE_THRESHOLD_SCHEMA,
                #             vol.Optional(CONF_ABOVE): cv.NUMERIC_STATE_THRESHOLD_SCHEMA,
                #             vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
                #             vol.Optional(CONF_FOR): cv.positive_time_period_template,
                #             vol.Optional(CONF_ATTRIBUTE): cv.match_all,
                mkDirectAttribute(HASS, hc.CONF_ABOVE, g, o_trigger, t)
                mkDirectAttribute(HASS, hc.CONF_BELOW, g, o_trigger, t)
                mkDirectAttribute(HASS, hc.CONF_ATTRIBUTE, g, o_trigger, t)
            elif t[cv.CONF_PLATFORM] == sun.const.DOMAIN:
                e_event = t[hc.CONF_EVENT]
                offset = t[hc.CONF_OFFSET]
                trigger_type = HASS["type/SunTrigger"]
                g.add((o_trigger, RDF.type, trigger_type))
                mkDirectAttribute(HASS, hc.CONF_EVENT, g, o_trigger, t)
                mkDirectAttribute(HASS, hc.CONF_OFFSET, g, o_trigger, t)
            elif t[cv.CONF_PLATFORM] == "mqtt":
                #         vol.Required(CONF_PLATFORM): mqtt.DOMAIN,
                #         vol.Required(CONF_TOPIC): mqtt.util.valid_subscribe_topic_template,
                #         vol.Optional(CONF_PAYLOAD): cv.template,
                #         vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
                #         vol.Optional(CONF_ENCODING, default=DEFAULT_ENCODING): cv.string,
                #         vol.Optional(CONF_QOS, default=DEFAULT_QOS): vol.All(
                #             vol.Coerce(int), vol.In([0, 1, 2])
                #         ),
                trigger_type = HASS["type/MQTTTrigger"]
                g.add((o_trigger, RDF.type, trigger_type))
                mkDirectAttribute(HASS, mqtt.device_trigger.CONF_TOPIC, g, o_trigger, t)
            else:
                logging.warning(f"not handling trigger platform {t[cv.CONF_PLATFORM]}: {t}.")
                continue
            # TODO: investigate warning on line below
            g.add((o_action_instance, HASS['hasTrigger'], o_trigger))

    for a_condition in a_config['condition']:
        for c in cv.CONDITION_SCHEMA(a_condition):
            pass  # TODO


def mkDirectAttribute(HASS, attribute, g, o_trigger, t):
    if attribute in t:
        g.add((o_trigger, HASS[attribute], Literal(t[attribute])))


def process_target_schema(HASS, MINE, g, o_action_instance, service_id, target):
    # TODO: The HASS['target'] here have no common superclass.
    if cv.ATTR_ENTITY_ID in target:
        for e in target[cv.ATTR_ENTITY_ID]:
            # This is a little bit tricky where we diverge from HA's modelling:
            #  We have a concrete instance already which corresponds to this particular pair of `service, target`.
            _, t_name = ha.split_entity_id(e)
            _, service_name = ha.split_entity_id(service_id)
            target_entity = MINE["service/" + mkname(t_name) + "_" + service_name]
            g.add((o_action_instance, HASS['target'], target_entity))
    if cv.ATTR_DEVICE_ID in target:
        for d in target[cv.ATTR_DEVICE_ID]:
            target_device = mkDevice(MINE, d)
            g.add((o_action_instance, HASS['target'], target_device))
    if cv.ATTR_AREA_ID in target:
        # untested:
        for a in target[cv.ATTR_AREA_ID]:
            target_area = mkLocationURI(MINE, a)
            g.add((o_action_instance, HASS['target'], target_area))


# TODO: unsused?!
def mkServiceURI(MINE, SAREF, service_id):
    _, service_name = ha.split_entity_id(service_id)
    if service_name == hc.SERVICE_TURN_ON:  # dupe TODO
        e_service_instance = SAREF["SwitchOnService"]
    else:
        e_service_instance = MINE["service/" + mkname(service_name.title()) + "_service"]
    return e_service_instance


def handle_entity(HASS, MINE, SAREF, class_to_saref, device: Optional[str], e, g, master):
    logging.info(f"Handling {e} for device {device}.")
    assert e.count('.') == 1
    (domain, e_name) = ha.split_entity_id(e)
    # Experimental section:
    # e_friendly_name = getYAML(f'state_attr("{e}", "friendly_name")')
    # END
    attrs = cs.getAttributes(e)
    device_class = attrs['device_class'] if 'device_class' in attrs else None
    e_d = None
    # if device is not None and domain not in class_to_saref:
    if domain not in class_to_saref:
        if device is None:
            c = HASS["platform/"+domain.title()]  # TODO: Review
            g.add((c, RDFS.subClassOf, HASS['platform/Platform']))
        else:
            # TODO
            c = SAREF['Device']
    else:
        c = class_to_saref[domain]
        # Nope out if `None`. Otherwise, use `domain` to instantiate, and remember the super-class (RHS) for later.
        if c is None:
            logging.warning(f"Skipping {e} (no mapping for domain {domain}).")
            return None  # Tell upstream.
        (subclass, super_class) = c
        if subclass:
            c = HASS[domain.title()]
        else:
            c = super_class
        if super_class == SAREF['Sensor']:  # XXX?
            # Special-casing (business rule):
            if device_class == SensorDeviceClass.TEMPERATURE:
                c = SAREF["TemperatureSensor"]
                # assert attrs['state_class'] == "measurement", attrs
            elif device_class == SensorDeviceClass.HUMIDITY:
                # TODO. Do we want more subclasses here?
                pass
                # c = HASS['HumiditySensor']
                # assert attrs['state_class'] == "measurement", attrs
            elif device_class == SensorDeviceClass.ENERGY:
                c = SAREF['Meter']
                # TODO -- probably we shouldn't be asserting those things.
                # assert attrs['state_class'] == "total_increasing", attrs
            else:
                # Spam:
                if device_class is not None:
                    logging.warning(f"Not handling class {device_class} for {e} (yet).")
            # END
    # https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/entity_registry.py
    e_d, e_name = mkEntityURI(MINE, e)
    g.add((e_d, RDF.type, c))
    try:
        friendly_name = cs.getAttributes(e)[hc.ATTR_FRIENDLY_NAME]
        # Bad idea, but...:
        g.add((e_d, RDFS.label, Literal(friendly_name)))
    except KeyError:
        pass

    # We're creating this reference to maybe analyse the mapping to SAREF later.
    #  Maybe the name or NS should be more outstanding? The interesting cases are
    # where the type is in HA and not a trivial subclass of SAREF:Device, or where
    # multiple platforms are projected onto the same SAREF Device.
    # TODO: Check with Fernando if this single instance here is an anti-pattern.
    # Or is our superclass like hass:Zone good enough?
    # Create instance (is MINE a good choice here?):
    g.add((MINE[domain.title() + "_platform"], RDF.type, HASS['platform/' + domain.title()]))
    g.add((e_d, HASS['provided_by'], MINE[domain.title() + "_platform"]))

    # Look up services this domain should have, and create them for this entity.
    features = attrs['supported_features'] if 'supported_features' in attrs else {}
    if domain in cs.getServices():
        for service in cs.getServices()[domain]:
            skip = False

            if domain == hc.Platform.BUTTON:
                pass
            elif domain == hc.Platform.CLIMATE:
                skip = service == climate.const.SERVICE_SET_FAN_MODE and not features & climate.ClimateEntityFeature.FAN_MODE
                skip |= service == climate.const.SERVICE_SET_HUMIDITY and not features & climate.ClimateEntityFeature.TARGET_HUMIDITY
                skip |= service == climate.const.SERVICE_SET_PRESET_MODE and not features & climate.ClimateEntityFeature.PRESET_MODE
                skip |= service == climate.const.SERVICE_SET_SWING_MODE and not features & climate.ClimateEntityFeature.SWING_MODE
            elif domain == hc.Platform.DEVICE_TRACKER:
                pass
            elif domain == hc.Platform.REMOTE:
                skip = service == remote.SERVICE_LEARN_COMMAND and not features & remote.RemoteEntityFeature.LEARN_COMMAND
                skip |= service == remote.SERVICE_DELETE_COMMAND and not features & remote.RemoteEntityFeature.DELETE_COMMAND
                # TODO: What about remote.RemoteEntityFeature.ACTIVITY?
            elif domain == hc.Platform.LIGHT:
                # Unclear how to handle light.LightEntityFeature.FLASH -- affects just args?
                pass
            else:
                pass  # TODO
            if skip:
                continue
            # Silly mapping, also see below.
            if service == hc.SERVICE_TURN_ON:
                s_class = SAREF["SwitchOnService"]
            else:
                s_class = HASS["service/" + service.title()]
            # TODO: constructed name is ... meh...
            serviceOffer(MINE, SAREF, e_d, e_name, g, "_" + service, s_class)

    # This part here maps HA platforms to SAREF-Device types.
    # Some HASS entities we can map to SAREF, others we just carry around inheriting
    #  from hass:Platform, because we can't  if they are saref:Devices. Or are they always?
    if domain == hc.Platform.SWITCH:
        # TODO: review double-typing.
        g.add((e_d, RDF.type, SAREF['Sensor']))  # because it would send notifications/"measure" the current setting?
        # e_function = MINE[mkname(e_name)+"_function"]  # TODO: name?
        # g.add((e_function, RDF.type, SAREF['OnOffFunction']))
        # g.add((e_d, SAREF['hasFunction'], e_function))
        pass
    elif domain == hc.Platform.BUTTON:
        pass  # OK, nothing in there.
    elif domain == hc.Platform.CLIMATE:
        # Business rule: https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/__init__.py#L214
        # Are we delivering temperature readings, e.g. an HVAC?
        # TODO: Don't use double-typing, but add sub-devices to parent?
        q = attrs[climate.ATTR_CURRENT_TEMPERATURE] if 'current_temperature' in attrs else None
        if q is not None:
            g.add((e_d, RDF.type, SAREF['TemperatureSensor']))
        q = attrs[climate.ATTR_CURRENT_HUMIDITY] if 'current_humidity' in attrs else None
        if q is not None:
            g.add((e_d, RDF.type, HASS['HumiditySensor']))
        # END
    elif domain == hc.Platform.BINARY_SENSOR or domain == hc.Platform.SENSOR:  # Handle both types in one for now.
        if device_class is not None:
            # Patch lower-case names:
            q = device_class.title()
            # Let's look it up in the SAREF "master-list":
            q_o = hasEntity(master, SAREF, 'Property', q)
            # TODO: we don't find the ones that we've already created ourselves,
            #  even in `g` that way!
            if q_o is None:
                # TODO: should search in the same way as `hasEntity` above.
                # Note: Still injects HASS types into instance (#5)
                q_o = createPropertyIfMissing(HASS, SAREF, g, q)
            # ...and instance:
            # TODO: should this be shared, ie. do we want different sensor measuring the same property?
            q_prop = MINE[f"{q}_prop"]
            g.add((q_prop, RDF.type, q_o))
            g.add((e_d, SAREF['measuresProperty'], q_prop))
        #
        q = attrs['unit_of_measurement'] if 'unit_of_measurement' in attrs else None
        if q is not None:
            # TODO - more below. When is this complete? When we've either exhausted SAREF or HASS.
            if device_class == SensorDeviceClass.TEMPERATURE:
                unit = SAREF['TemperatureUnit']
            elif device_class == SensorDeviceClass.CURRENT:
                unit = SAREF['PowerUnit']
            elif device_class == SensorDeviceClass.POWER:
                unit = SAREF['PowerUnit']
            elif device_class == SensorDeviceClass.ENERGY:
                unit = SAREF['EnergyUnit']
            elif device_class == SensorDeviceClass.PRESSURE:
                unit = SAREF['PressureUnit']
            else:  # Not built-in.
                # TODO: check -- are we done here?
                # Should get them from HA core statically. (#5)
                unit = HASS[mkname(q)]
                g.add((unit, RDFS.subClassOf, SAREF['UnitOfMeasure']))
                # TODO: add Measurement/Property
            g.add((MINE[mkname(q)], RDF.type, unit))
    elif domain == hc.Platform.LIGHT:
        pass  # Ok
    elif domain == hc.Platform.WEATHER:
        # TODO: Loooots of attributes
        pass
    else:
        logging.warning(f"not really handling platform {domain}/{e}.")
    return e_d


def createPropertyIfMissing(HASS, SAREF, g, q):
    q_o = HASS[q]
    uri = URIRef("http://home-assistant.io/" + q)
    # TODO: is this worth the effort?
    if len(list(g.triples((uri, None, None)))) == 0:
        logging.info(f"Creating {q}.")
        # Create Property...
        g.add((q_o, RDFS.subClassOf, SAREF['Property']))
    return q_o


def serviceOffer(MINE, SAREF, e_d, e_name, g, suffix, svc_obj):
    e_service_inst = MINE["service/" + mkname(e_name) + suffix]
    g.add((e_service_inst, RDF.type, svc_obj))
    g.add((e_d, SAREF['offers'], e_service_inst))


def getEntitiesWODevice():
    for k in cs.getStates():
        # Weird...you'd think they'd care 'device_id' around, but they don't:
        if cs.getDeviceId(k['entity_id']) == 'None':
            yield k


def mkServiceToDomainTable():
    hass_svcs = {}
    for component_name, svc_services in cs.getServices().items():
        for s in svc_services:
            if s in hass_svcs:
                t = hass_svcs[s]
                t.add(component_name)
                hass_svcs[s] = t
            else:
                hass_svcs[s] = {component_name}
    return hass_svcs


@cache
def hasEntity(graph, ns, cl, q):
    # TODO: At least we're caching now...but we could precompute a dictionary.
    # Can't search in e.g. HA_ACTION?
    for s, _, _ in graph.triples((None, RDFS.subClassOf, ns[cl])):
        if s.endswith("/" + q):
            return ns[q]
    return None


def setupSAREF(g, importsOnly=False):
    SAREF = Namespace("https://saref.etsi.org/core/")
    S4BLDG = Namespace("https://saref.etsi.org/saref4bldg/")
    HASS = Namespace("https://www.foldr.org/profiles/homeassistant/")
    HASS_ACTION = HASS.term("action/")
    g.bind("saref", SAREF)
    g.bind("owl", OWL)
    g.bind("s4bldg", S4BLDG)
    g.bind("hass", HASS)
    g.bind("ha_action", HASS_ACTION)
    MINE = Namespace("http://my.name.spc/")
    MINE_ACTION = Namespace("http://my.name.spc/action/")
    MINE_AUTOMATION = Namespace("http://my.name.spc/automation/")
    MINE_ENTITY = Namespace("http://my.name.spc/entity/")
    MINE_SERVICE = Namespace("http://my.name.spc/service/")
    if importsOnly:
        saref_import = URIRef(str(MINE))
    else:
        saref_import = URIRef(str(HASS))
    g.add((saref_import, RDF.type, OWL.Ontology))
    g.add((saref_import, OWL.imports, URIRef(str(SAREF))))
    g.add((saref_import, OWL.imports, URIRef(str(S4BLDG))))

    if importsOnly:
        return MINE, HASS, SAREF, S4BLDG, None

    g.bind("mine", MINE)
    g.bind("action", MINE_ACTION)
    g.bind("automation", MINE_AUTOMATION)
    g.bind("entity", MINE_ENTITY)
    g.bind("service", MINE_SERVICE)

    # Experimental
    # Manual entities, e.g. from "lights":

    # TODO: light maybe_has brightness?
    g.add((HASS['Brightness'], RDFS.subClassOf, SAREF['Property']))

    # Inject Service-classes
    # Note that using /api/services only gives you the services of your instance. Here, we want to create
    #  the metamodel/profile for HA, so we use the CSV generated from a git-checkout.
    # We still need a static map to SAREF.

    hass_svcs = mkServiceToDomainTable()
    for s, domains in hass_svcs.items():
        # This is weird: SAREF has SwitchOnService -- only:
        if s == hc.SERVICE_TURN_ON:
            continue  # special case...
        g.add((HASS["service/" + s.title()], RDFS.subClassOf, SAREF['Service']))
        # WIP -- do we want to inject ALL HASS classes below the corresponding SAREF devices?
        # for d in domains:
        #    print(s,d)
        #    g.add((HASS[s], MINE['provided'], HASS[d]))

    # Let's patch SAREF a bit with our extensions:
    # True/False could be replace by having the HASS-ns on the RHS.
    class_to_saref = {
        hc.Platform.AIR_QUALITY: (True, SAREF["Sensor"]),
        hc.Platform.ALARM_CONTROL_PANEL: (True, SAREF["Device"]),
        hc.Platform.BINARY_SENSOR: (False, SAREF["Sensor"]),  # Modelling design decisions...
        hc.Platform.BUTTON: (True, SAREF["Sensor"]),
        hc.Platform.CALENDAR: None,
        hc.Platform.CAMERA: (True, SAREF["Device"]),
        hc.Platform.CLIMATE: (False, SAREF["HVAC"]),
        hc.Platform.COVER: (True, SAREF["Actuator"]),  # ? saref4bldg:ShadingDevice
        hc.Platform.DEVICE_TRACKER: (True, SAREF["Sensor"]),
        hc.Platform.FAN: (True, SAREF["Appliance"]),
        hc.Platform.GEO_LOCATION: None,
        hc.Platform.HUMIDIFIER: (True, SAREF["Appliance"]),
        hc.Platform.IMAGE_PROCESSING: None,
        hc.Platform.LIGHT: (True, SAREF["Appliance"]),
        hc.Platform.LOCK: (True, SAREF["Appliance"]),
        hc.Platform.MAILBOX: None,
        hc.Platform.MEDIA_PLAYER: (True, SAREF["Appliance"]),
        hc.Platform.NOTIFY: None,
        hc.Platform.NUMBER: None,  # SERVICE_SET_VALUE
        hc.Platform.REMOTE: (True, SAREF["Device"]),
        hc.Platform.SCENE: None,
        hc.Platform.SELECT: None,  # SERVICE_SELECT_OPTION
        hc.Platform.SENSOR: (False, SAREF["Sensor"]),
        hc.Platform.SIREN: (True, SAREF["Appliance"]),
        hc.Platform.STT: None,
        hc.Platform.SWITCH: (False, SAREF["Switch"]),  # Modelling.
        hc.Platform.TEXT: None,
        hc.Platform.TTS: None,
        hc.Platform.UPDATE: None,
        hc.Platform.VACUUM: (True, SAREF["Appliance"]),
        hc.Platform.WATER_HEATER: (True, SAREF["Appliance"]),
        hc.Platform.WEATHER: (True, SAREF["Sensor"]),
        # Not a `platform`:
        "device": (False, SAREF["Device"]),  # of course...
    }
    for p, v in class_to_saref.items():
        if v is not None:
            flag, superclass = v
            if flag:
                g.add((HASS[p.title()], RDFS.subClassOf, superclass))
    # END

    # Proper HASS-contribution to SAREF:
    # TODO: Review with Fernando -- we could introduce HASS['Entity'] and double-type.
    prop = HASS['via_device']
    g.add((prop, RDF.type, OWL.ObjectProperty))
    g.add((prop, RDFS.domain, SAREF['Device']))
    g.add((prop, RDFS.range, SAREF['Device']))
    #

    # BEGIN SCHEMA metadata, reflection on
    #  https://github.com/home-assistant/core/blob/9f7fd8956f22bd873d14ae89460cdffe6ef6f85d/homeassistant/helpers/config_validation.py#L1641
    ha_action = HASS['action/Action']
    # Automation consistsOf (order) Actions
    h_prov = HASS['consistsOf']
    g.add((h_prov, RDF.type, OWL.ObjectProperty))
    g.add((h_prov, OWL.inverseOf, HASS['belongsTo']))
    g.add((h_prov, RDFS.domain, HASS['Automation']))
    g.add((h_prov, RDFS.range, ha_action))
    for k, v in cv.ACTION_TYPE_SCHEMAS.items():
        g.add((HASS["action/" + k.title()], RDFS.subClassOf, ha_action))
    # END

    tt = HASS["type/TriggerType"]
    prop_has_trigger = HASS['hasTrigger']
    g.add((prop_has_trigger, RDF.type, OWL.ObjectProperty))
    g.add((prop_has_trigger, RDFS.domain, HASS['action/Action']))
    g.add((prop_has_trigger, RDFS.range, tt))

    prop_has_entity = HASS['trigger_entity']
    g.add((prop_has_entity, RDF.type, OWL.ObjectProperty))
    g.add((prop_has_entity, RDFS.domain, tt))
    g.add((prop_has_entity, RDFS.range, SAREF['Device']))

    trigger_type = HASS["type/NumericStateTrigger"]
    g.add((trigger_type, RDFS.subClassOf, HASS["type/TriggerType"]))
    trigger_type = HASS["type/MQTTTrigger"]
    g.add((trigger_type, RDFS.subClassOf, HASS["type/TriggerType"]))
    trigger_type = HASS["type/StateTrigger"]
    g.add((trigger_type, RDFS.subClassOf, HASS["type/TriggerType"]))

    zt = HASS["type/ZoneTrigger"]
    g.add((zt, RDFS.subClassOf, tt))
    # TODO: Uhhh...forgot?
    # prop = HASS['event']
    # g.add((prop, RDF.type, OWL.DatatypeProperty))
    # g.add((zt, RDFS.subClassOf, prop))
    prop = HASS['zone']
    g.add((prop, RDF.type, OWL.ObjectProperty))
    g.add((prop, RDFS.domain, zt))
    g.add((prop, RDFS.range, SAREF['Device']))

    st = HASS["type/SunTrigger"]
    g.add((st, RDFS.subClassOf, tt))
    # Event, Offset

    # Model platforms -- unclear if we'll really need this in the future,
    #  but useful for i) a complete metamodel ii) for cross-referencing.
    ha_platform = HASS['platform/Platform']
    h_prov = HASS['provided_by']
    g.add((h_prov, RDF.type, OWL.ObjectProperty))
    g.add((h_prov, OWL.inverseOf, HASS['provides']))
    # Actually, a HASS-entity! REVIEW, must be subclass of saref:device in hass-ns!
    # TODO: Talk with Fernando about this.
    g.add((h_prov, RDFS.domain, SAREF['Device']))
    g.add((h_prov, RDFS.range, ha_platform))

    for p in hc.Platform:
        g.add((HASS['platform/' + p.title()], RDFS.subClassOf, ha_platform))
    # END

    return MINE, HASS, SAREF, S4BLDG, class_to_saref


if __name__ == "__main__":
    main()
