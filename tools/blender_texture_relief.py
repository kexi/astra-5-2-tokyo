"""Give inferred building finishes metric-scale grain, roughness and window polish."""

import bpy

NAME = "PLATEAU · Building paint relief"


def relief_group():
    known = bpy.data.node_groups.get(NAME)
    if known:
        return known
    graph = bpy.data.node_groups.new(NAME, "ShaderNodeTree")
    for name, kind, direction in (
        ("Paint", "Color", "INPUT"),
        ("Original roughness", "Float", "INPUT"),
        ("Underlying normal", "Vector", "INPUT"),
        ("Roughness", "Float", "OUTPUT"),
        ("Normal", "Vector", "OUTPUT"),
    ):
        graph.interface.new_socket(
            name=name, in_out=direction, socket_type="NodeSocket" + kind
        )

    def node(kind, label, x, y):
        item = graph.nodes.new(kind)
        item.label = label
        item.location = x, y
        return item

    def plug(value, dest):
        if isinstance(value, bpy.types.NodeSocket):
            graph.links.new(value, dest)
        else:
            dest.default_value = value

    def calc(op, a, b, label, x, y):
        n = node("ShaderNodeMath", label, x, y)
        n.operation = op
        plug(a, n.inputs[0])
        plug(b, n.inputs[1])
        return n.outputs[0]

    def mix(factor, first, second, label, x, y):
        n = node("ShaderNodeMix", label, x, y)
        n.data_type = "FLOAT"
        plug(factor, n.inputs[0])
        plug(first, n.inputs[2])
        plug(second, n.inputs[3])
        return n.outputs[0]

    inputs = node("NodeGroupInput", "棟別の塗装と既存の表面", 0, 0).outputs
    kind = node("ShaderNodeAttribute", "各面の出典区分", 0, 430)
    kind.attribute_name = "TextureKind"
    face = kind.outputs["Fac"]
    applied = calc("GREATER_THAN", face, 0.5, "作成・補修した面", 230, 510)
    infill = calc("COMPARE", face, 1, "外壁", 230, 360)
    repaired = calc("COMPARE", face, 4, "補修した外壁", 230, 225)
    wall = calc("MAXIMUM", infill, repaired, "鉛直仕上げ", 450, 360)
    sep = node("ShaderNodeSeparateColor", "棟別マップの塗色", 220, 40)
    plug(inputs["Paint"], sep.inputs[0])
    cool = calc(
        "SUBTRACT", sep.outputs["Blue"], sep.outputs["Red"], "寒色の窓材", 450, 70
    )
    mask = node("ShaderNodeMapRange", "窓と外壁材の移行", 650, 85)
    mask.clamp = True
    mask.interpolation_type = "SMOOTHSTEP"
    plug(cool, mask.inputs["Value"])
    mask.inputs["From Min"].default_value = 0.012
    mask.inputs["From Max"].default_value = 0.065
    pane = calc("MULTIPLY", wall, mask.outputs[0], "外壁のガラスのみ", 860, 210)
    coords = node("ShaderNodeTexCoord", "建物ローカル（m）", 250, -255)
    noise = node("ShaderNodeTexNoise", "3次元の細かな骨材と塗装", 500, -255)
    noise.inputs["Scale"].default_value = 24
    noise.inputs["Detail"].default_value = 2
    plug(coords.outputs["Object"], noise.inputs["Vector"])
    mineral = mix(noise.outputs["Fac"], 0.67, 0.86, "粗い鉱物面", 755, -200)
    roughness = mix(pane, mineral, 0.22, "窓の研磨面", 1080, 215)
    finish = mix(
        applied,
        inputs["Original roughness"],
        roughness,
        "観測面の既存材質を保持",
        1290,
        360,
    )
    grain = calc("SUBTRACT", 1.0, pane, "ガラスの微細凹凸を抑える", 1050, -75)
    active = calc("MULTIPLY", grain, applied, "生成面だけの凹凸", 1260, -100)
    bump = node("ShaderNodeBump", "仕上げの微細凹凸（0.45 mm）", 1470, -120)
    bump.inputs["Distance"].default_value = 0.00045
    plug(active, bump.inputs["Strength"])
    plug(noise.outputs["Fac"], bump.inputs["Height"])
    plug(inputs["Underlying normal"], bump.inputs["Normal"])
    out = node("NodeGroupOutput", "表面", 1720, 160)
    plug(finish, out.inputs["Roughness"])
    plug(bump.outputs["Normal"], out.inputs["Normal"])
    return graph


def bind_infill(material, paint_node):
    tree = material.node_tree
    surface = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    group = tree.nodes.new("ShaderNodeGroup")
    group.node_tree = relief_group()
    group.location = surface.location.x - 260, surface.location.y - 290
    tree.links.new(paint_node.outputs["Color"], group.inputs["Paint"])
    for name, background in (
        ("Roughness", "Original roughness"),
        ("Normal", "Underlying normal"),
    ):
        socket = surface.inputs[name]
        if socket.is_linked:
            tree.links.new(socket.links[0].from_socket, group.inputs[background])
        elif name == "Normal":
            geometry = tree.nodes.new("ShaderNodeNewGeometry")
            geometry.location = group.location.x - 220, group.location.y - 150
            tree.links.new(geometry.outputs["Normal"], group.inputs[background])
        else:
            group.inputs[background].default_value = socket.default_value
        tree.links.new(group.outputs[name], socket)
