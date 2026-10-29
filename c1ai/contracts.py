"""Small, explicit request contracts shared by all frontends."""
import hashlib
import json
import math

MODULES = {
    "exposure": ("exposure", "contrast", "brightness", "saturation"),
    "white_balance": ("temperature", "tint"),
    "hdr": ("highlight_recovery", "shadow_recovery", "white_recovery", "black_recovery"),
}
FIELDS = tuple(field for fields in MODULES.values() for field in fields)
STRUCTURED_MODULES = {"curves", "levels", "color_mixer", "color_balance", "layers", "masks", "film_grain"}
COMMANDS = {"context", "read", "adjust", "images", "preview", "operations"}
IMAGE_ACTIONS = {"clone", "create_candidate", "select", "next", "previous", "prepare_adjustments", "prepare_tool"}


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def layer_ref(value):
    """An observed, session-bound reference; a name or index is never an ID."""
    if (not isinstance(value, dict) or not isinstance(value.get("runtime_id"), list)
            or not value["runtime_id"] or len(value["runtime_id"]) > 32
            or any(isinstance(n, bool) or not isinstance(n, int) for n in value["runtime_id"])
            or not isinstance(value.get("name"), str)
            or not isinstance(value.get("is_image_layer"), bool)):
        raise Refused("invalid_layer_ref", "Use the complete layer reference returned by the current observation")
    digest(value)
    return value


def validate_structured_patch(request):
    """Bounded public model, independently of adapter/live qualification."""
    module, patch = request.get("module"), request.get("patch")
    if module not in STRUCTURED_MODULES or not isinstance(patch, dict) or not patch:
        raise Refused("invalid_patch", "A supported structured module and nonempty patch are required")

    def keys(required, optional=()):
        if not set(required) <= patch.keys() or patch.keys() - set(required) - set(optional):
            raise Refused("invalid_patch", "The module patch has missing or unsupported fields")

    def number(value, lower, upper):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not lower <= value <= upper):
            raise Refused("invalid_value", f"Expected a finite number between {lower} and {upper}")

    if module == 'film_grain':
        keys(set(), {'type','impact','granularity'})
        if not patch:raise Refused('invalid_patch','Film grain requires at least one parameter')
        if 'type' in patch and patch['type'] not in {'fine','silver_rich','soft','cubic','tabular','harsh'}:
            raise Refused('invalid_patch','Unsupported native film grain type')
        for field in ('impact','granularity'):
            if field in patch:number(patch[field],0,100)
        return patch
    if module == "levels":
        ranges={'input_black':(0,255),'input_white':(0,255),'midtone':(-1,1),'output_black':(0,255),'output_white':(0,255)}
        keys({'channel'},ranges)
        if patch['channel'] not in {'rgb','red','green','blue'} or not patch.keys() & ranges.keys():
            raise Refused('invalid_patch','Levels require a channel and at least one value')
        for field in patch.keys() & ranges.keys():number(patch[field],*ranges[field])
    elif module == "curves":
        keys({"channel", "points"})
        if patch["channel"] not in {"rgb", "red", "green", "blue", "luma"}:
            raise Refused("invalid_patch", "Unsupported curve channel")
        points = patch["points"]
        if not isinstance(points, list) or not 2 <= len(points) <= 64:
            raise Refused("invalid_patch", "A curve requires 2 to 64 ordered normalized points")
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise Refused("invalid_patch", "Each curve point must be [x, y]")
            for value in point:
                number(value, 0, 1)
        if points[0][0] != 0 or points[-1][0] != 1 or any(a[0] >= b[0] for a, b in zip(points, points[1:])):
            raise Refused("invalid_patch", "Curve x coordinates must increase strictly from 0 to 1")
    elif module in {"color_mixer", "color_balance"}:
        if module == "color_mixer":
            mode=patch.get('mode')
            if mode in {'advanced','skin'}:
                action=patch.get('action','update')
                ranges={'hue':(-30,30),'saturation':(-100,200),'lightness':(-100,100),'smoothness':(1,30)} if mode=='advanced' else {
                    'hue':(-5,5),'saturation':(-30,30),'lightness':(-10,10),'smoothness':(1,30),
                    'uniformity_hue':(0,100),'uniformity_saturation':(0,100),'uniformity_lightness':(0,100)}
                ranges.update(hue_low=(-357,-2),hue_high=(2,357),saturation_low=(-3.4028234663852886e38,-2),saturation_high=(2,3.4028234663852886e38))
                if action not in {'add','update','remove'}:raise Refused('invalid_patch','Use add/update/remove for a mixer range')
                required={'mode','rgb'} if action=='add' else {'mode','index'}
                keys(required,{'action','rgb','enabled','inverted'}|ranges.keys() if action!='remove' else {'action'})
                if 'index' in patch and (type(patch['index']) is not int or patch['index']<0):raise Refused('invalid_patch','Range index must be a nonnegative integer from the observation')
                if 'rgb' in patch:
                    if not isinstance(patch['rgb'],list) or len(patch['rgb'])!=3:raise Refused('invalid_patch','rgb requires three native device values')
                    for v in patch['rgb']:number(v,0,255)
                for k in ('enabled','inverted'):
                    if k in patch and type(patch[k]) is not bool:raise Refused('invalid_value','Mixer flags must be booleans')
                for k in patch.keys() & ranges.keys():number(patch[k],*ranges[k])
                if action=='update' and not patch.keys() & (ranges.keys()|{'rgb','enabled','inverted'}):raise Refused('invalid_patch','Range update requires at least one value')
                return patch
            keys({"mode", "color"}, {"hue", "saturation", "lightness"})
            if mode != "basic" or patch["color"] not in {
                    "red", "orange", "yellow", "green", "cyan", "blue", "purple", "magenta", "all"}:
                raise Refused("invalid_patch", "Only explicit basic mixer sectors are supported")
            if patch["color"] == "all" and "lightness" in patch:
                raise Refused("invalid_patch", "The all-colors mixer row has no qualified lightness field")
            ranges = {"hue": (-30, 30), "saturation": (-80, 80), "lightness": (-50, 50)}
        else:
            keys({"wheel"}, {"x", "y", "lightness"})
            if patch["wheel"] not in {"master", "shadows", "midtones", "highlights"}:
                raise Refused("invalid_patch", "Unsupported color balance wheel")
            if patch["wheel"] == "master" and "lightness" in patch:
                raise Refused("invalid_patch", "The master wheel has no qualified lightness field")
            ranges = {name: (-50, 50) for name in ("x", "y", "lightness")}
        if not patch.keys() & ranges.keys():
            raise Refused("invalid_patch", "At least one adjustment value is required")
        for name in patch.keys() & ranges.keys():
            number(patch[name], *ranges[name])
    elif module == "layers":
        action = patch.get("action")
        required = {"create_empty": {"action"}, "select": {"action", "layer_ref"},
                    "remove_created": {"action", "layer_ref", "creation_operation_id"},
                    "set_opacity": {"action", "layer_ref", "opacity"},
                    "rename": {"action", "layer_ref", "name"}, "set_enabled": {"action", "layer_ref", "enabled"},
                    "duplicate": {"action", "layer_ref"}, "move": {"action", "layer_ref", "index"}}
        if action not in required:
            raise Refused("invalid_patch", "Unsupported layer action")
        keys(required[action], {"name"} if action == "duplicate" else ())
        if "layer_ref" in patch:
            layer_ref(patch["layer_ref"])
        if action == "remove_created" and (not isinstance(patch["creation_operation_id"], str)
                                           or not patch["creation_operation_id"].strip()):
            raise Refused("invalid_patch", "Removing a created layer requires its creation operation")
        if action == "set_opacity":
            number(patch["opacity"], 0, 100)
        if 'name' in patch and (not isinstance(patch['name'],str) or not patch['name'].strip() or len(patch['name'])>128 or any(ord(c)<32 for c in patch['name'])):
            raise Refused('invalid_patch','Layer name must contain 1..128 printable characters')
        if action=='set_enabled' and type(patch['enabled']) is not bool:raise Refused('invalid_patch','enabled must be boolean')
        if action=='move' and (type(patch['index']) is not int or patch['index']<0):raise Refused('invalid_patch','Use a zero-based adjustment-layer index')
    else:
        action=patch.get('action')
        required={'create_subject':set(),'create_background':set(),'invert':set(),'clear':set(),
            'fill':{'density'},'copy':{'source_layer'},'combine':{'source_layer','mode'},'feather':set(),
            'linear_gradient':{'start','stop','midpoint'},'radial_gradient':{'center','radius_x','radius_y','falloff','angle','inverted'},
            'luma_range':{'shadow','shadow_falloff','highlight','highlight_falloff','radius','sensitivity','inverted'},'select_submask':{'index'}}
        if action not in required:raise Refused('invalid_patch','Unsupported native mask action')
        keys({'action'}|required[action], {'radius','native_amount'} if action=='feather' else ())
        if action=='feather':
            if ('radius' in patch)==('native_amount' in patch):raise Refused('invalid_patch','Feather requires exactly one of pixel radius or native_amount')
            if 'native_amount' in patch:number(patch['native_amount'],0,100)
        if 'source_layer' in patch:layer_ref(patch['source_layer'])
        if action=='combine' and patch['mode'] not in {'add','subtract','intersect'}:raise Refused('invalid_patch','Use add/subtract/intersect')
        if 'radius' in patch:number(patch['radius'],0,300)
        if 'density' in patch:number(patch['density'],0,1)
        if 'inverted' in patch and type(patch['inverted']) is not bool:raise Refused('invalid_patch','inverted must be boolean')
        for key in ('start','stop','center'):
            if key in patch:
                if not isinstance(patch[key],list) or len(patch[key])!=2:raise Refused('invalid_patch','Gradient coordinates are normalized [x,y]')
                for value in patch[key]:number(value,0,1)
        if action=='linear_gradient':
            number(patch['midpoint'],.01,.99)
            if patch['start']==patch['stop']:raise Refused('invalid_patch','Gradient endpoints must differ')
        if action=='radial_gradient':
            number(patch['radius_x'],.001,1);number(patch['radius_y'],.001,1);number(patch['falloff'],0,1);number(patch['angle'],-360,360)
        if action=='luma_range':
            for key in ('shadow','shadow_falloff','highlight','highlight_falloff'):number(patch[key],0,255)
            number(patch['sensitivity'],0,100)
            if not patch['shadow_falloff']<=patch['shadow']<=patch['highlight']<=patch['highlight_falloff']:raise Refused('invalid_patch','Luma endpoints must be ordered falloff/shadow/highlight/falloff')
        if action=='select_submask' and (type(patch['index']) is not int or patch['index']< -1):raise Refused('invalid_patch','Use -1 for the whole mask or a current submask index')
    return patch


def validate_module_action(request):
    patch=request.get('patch',{});action=patch.get('action')
    if request.get('module') not in set(MODULES)|{'curves','levels','color_mixer','color_balance','film_grain'}:
        raise Refused('invalid_patch','Only color adjustment modules can be reset or pasted')
    expected={'action'} if action=='reset' else {'action','source_observation_token'}
    if action not in {'reset','paste'} or set(patch)!=expected:
        raise Refused('invalid_patch','Use reset or paste with a source_observation_token')
    if action=='paste' and (not isinstance(patch['source_observation_token'],str) or not patch['source_observation_token']):
        raise Refused('invalid_patch','Paste requires a complete source module read')
    return patch


def fields_for(request):
    if "fields" in request:
        fields = request["fields"]
    else:
        modules = request.get("modules", [request["module"]] if "module" in request else list(MODULES))
        if not isinstance(modules, list) or any(module not in MODULES for module in modules):
            raise Refused("unsupported", "Unsupported adjustment module")
        fields = [field for module in modules for field in MODULES[module]]
    if not isinstance(fields, list) or any(field not in FIELDS for field in fields):
        raise Refused("invalid_fields", "Expected a list of supported fields; [] reads identity only")
    return list(dict.fromkeys(fields))


def validate_patch_schema(request):
    module, patch = request.get("module"), request.get("patch")
    if module not in MODULES:
        raise Refused("unsupported", "This module has no implemented numeric contract")
    if not isinstance(patch, dict) or not patch:
        raise Refused("invalid_patch", "patch must be a nonempty object")
    for field, value in patch.items():
        if field not in MODULES[module]:
            raise Refused("wrong_module", "Every patch field must belong to the selected module")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise Refused("invalid_value", "Adjustment values must be finite numbers")
    return patch


def validate_patch(request, metadata):
    patch = validate_patch_schema(request)
    for field, value in patch.items():
        capability = metadata.get(field, {})
        if capability.get("writable") is not True:
            raise Refused("unsupported", f"Field is not qualified for writing: {field}")
        if value < capability.get("min", -math.inf) or value > capability.get("max", math.inf):
            raise Refused("out_of_range", f"Value is outside the backend range: {field}")
        precision = capability.get("precision")
        if precision and not math.isclose(value / precision, round(value / precision), abs_tol=1e-7, rel_tol=0):
            raise Refused("unsupported_precision", f"Value exceeds the backend field precision: {field}")
    return patch
