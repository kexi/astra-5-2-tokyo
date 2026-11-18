"""Add a photo-driven dielectric window finish to imported PLATEAU building materials."""

import bpy

GROUP_NAME = "PLATEAU · Facade glazing"


def _glazing_group():
    existing = bpy.data.node_groups.get(GROUP_NAME)
    if existing:
        return existing

    group = bpy.data.node_groups.new(GROUP_NAME, "ShaderNodeTree")
    for name, direction, kind in (
        ("Facade photo", "INPUT", "Color"),
        ("Original roughness", "INPUT", "Float"),
        ("Original coat", "INPUT", "Float"),
        ("Roughness", "OUTPUT", "Float"),
        ("Coat weight", "OUTPUT", "Float"),
    ):
        group.interface.new_socket(
            name=name, in_out=direction, socket_type="NodeSocket" + kind
        )

    def node(kind, label, x, y):
        result = group.nodes.new(kind)
        result.label = label
        result.location = (x, y)
        return result

    def connect(value, socket):
        if isinstance(value, bpy.types.NodeSocket):
            group.links.new(value, socket)
        else:
            socket.default_value = value

    def math(operation, a, b, label, x, y):
        result = node("ShaderNodeMath", label, x, y)
        result.operation = operation
        connect(a, result.inputs[0])
        connect(b, result.inputs[1])
        return result.outputs[0]

    def ramp(value, low, high, y0, y1, label, x, y):
        result = node("ShaderNodeMapRange", label, x, y)
        result.interpolation_type = "SMOOTHERSTEP"
        result.clamp = True
        connect(value, result.inputs["Value"])
        for key, number in zip(
            ("From Min", "From Max", "To Min", "To Max"), (low, high, y0, y1)
        ):
            result.inputs[key].default_value = number
        return result.outputs[0]

    inputs = node("NodeGroupInput", "Original imported material", 0, 0).outputs
    split = node("ShaderNodeSeparateColor", "Linear facade colour", 200, 200)
    group.links.new(inputs["Facade photo"], split.inputs[0])
    light = node("ShaderNodeRGBToBW", "Photo luminance", 200, -40)
    group.links.new(inputs["Facade photo"], light.inputs[0])
    denominator = math("ADD", light.outputs[0], 0.045, "Lift deep shadow", 430, -90)
    cool_channel = math(
        "MAXIMUM", split.outputs[1], split.outputs[2], "Green or blue", 430, 240
    )
    cool_delta = math(
        "SUBTRACT", cool_channel, split.outputs[0], "Reject warm brick", 630, 240
    )
    cool_ratio = math(
        "DIVIDE", cool_delta, denominator, "Normalised cool tint", 830, 240
    )
    cool = ramp(cool_ratio, -0.08, 0.2, 0.0, 1.0, "Cool window colour", 1030, 240)
    dark = ramp(light.outputs[0], 0.035, 0.26, 1.0, 0.0, "Dark glazing", 830, -70)
    pane = math("MULTIPLY", cool, dark, "Glazing colour mask", 1250, 190)

    geometry = node("ShaderNodeNewGeometry", "World-space face", 420, -410)
    normal = node("ShaderNodeSeparateXYZ", "World up", 630, -410)
    group.links.new(geometry.outputs["True Normal"], normal.inputs[0])
    slope = math("ABSOLUTE", normal.outputs["Z"], 0.0, "Absolute slope", 830, -410)
    wall = ramp(slope, 0.15, 0.53, 1.0, 0.0, "Vertical walls only", 1030, -410)
    mask = math("MULTIPLY", pane, wall, "Glazed facade", 1460, 130)

    mix = node("ShaderNodeMix", "Polished panes · original walls", 1660, 200)
    mix.data_type = "FLOAT"
    group.links.new(mask, mix.inputs[0])
    group.links.new(inputs["Original roughness"], mix.inputs[2])
    mix.inputs[3].default_value = 0.18
    coat = math(
        "MAXIMUM", inputs["Original coat"], mask, "Dielectric surface coat", 1660, -80
    )
    output = node("NodeGroupOutput", "Window finish", 1880, 140)
    group.links.new(mix.outputs[0], output.inputs["Roughness"])
    group.links.new(coat, output.inputs["Coat weight"])
    return group


def enhance_building_material(material):
    """Return True when a textured Principled material receives the shared glazing group."""
    tree = material.node_tree
    if tree is None or any(n.name == GROUP_NAME for n in tree.nodes):
        return False
    principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None or not principled.inputs["Base Color"].is_linked:
        return False
    if not any(n.type == "TEX_IMAGE" and n.image for n in tree.nodes):
        return False

    finish = tree.nodes.new("ShaderNodeGroup")
    finish.node_tree = _glazing_group()
    finish.name = finish.label = GROUP_NAME
    finish.width = 230
    finish.location = (principled.location.x - 300, principled.location.y - 350)
    photo = principled.inputs["Base Color"].links[0].from_socket
    tree.links.new(photo, finish.inputs["Facade photo"])
    for target, original, output in (
        ("Roughness", "Original roughness", "Roughness"),
        ("Coat Weight", "Original coat", "Coat weight"),
    ):
        socket = principled.inputs[target]
        if socket.is_linked:
            tree.links.new(socket.links[0].from_socket, finish.inputs[original])
        else:
            finish.inputs[original].default_value = socket.default_value
        tree.links.new(finish.outputs[output], socket)
    return True
