#!/usr/bin/python3

from docopt import docopt

import json
import operator
import os
import sys
import textwrap


doc = """
Usage:
    spine-to-godot-scene.py (-h | --help)
    spine-to-godot-scene.py <json>... (-t <node_type>) (-n <node_name>) [-e <file_name>] [-s <script>]

Options:
    -h, --help          This help here
    <json>...           JSON file(s) to include
    -t <node_type>      Node type to use
    -n <node_name>      Name of the node, eg. "player"
    -e <file_name>      Extra child data from file. Must hard-code index="{index}".
    -s <script>         Attach script to node

Output goes to stdout. You should pipe it to a pager for cursory review before
redirecting it to a file.

If you're creating a KinematicBody2D, you can add the CollisionShape2D by writing
its lines in a file and passing it to `-e`. Its "{index}" will be fixed on output.
This is hacky and you don't necessarily want to try it, but create those types of
children manually.

After running this script:
  * You must most likely set the skin on your Spine node(s).
  * If you must scale the animation, do it on the Spine node(s), not the main node.
  * If you use Escoria, and added a script here, set the properties, like `animations`.

Because the names will be autogenerated and annoying, a good practice is to go to
the `AnimationPlayer` and make copies of autogenerated animations and rename them.

This is useful to create short-hands like `skin_blue` and to add `visible` keyframes
into animations that involve moving a character node around when you have different
Spine nodes for directions.
"""


class ExtResource:
    """These describe eg. the paths to Spine json files
    """

    def __init__(self, *, resource_path=None, resource_type=None):
        self.resource_path = resource_path
        self.resource_type = resource_type
        self.i = None

    def __str__(self):
        return textwrap.dedent("""\
            [ext_resource path="res://{path}" type="{type}" id={i}]
            """.format(path=self.resource_path, type=self.resource_type, i=self.i))


class SubResourceAnimation:
    """Base class for all SubResources that are Animation
    """

    resource_type = 'Animation'


class SubResourceSpine(SubResourceAnimation):
    """Provides the constructor for SubResources that are relevant to Spine Animation
    """

    def __init__(self, *, resource_name=None, node_name=None, data=None):
        self.base_resource_name = resource_name
        self.node_name = node_name
        self.i = None

        # This is just to piggyback eg. Spine json data, nothing formal
        self.data = data


class SubResourceSpinePlay(SubResourceSpine):
    """Sub-resources, but only Animations are supported at all
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # This naming scheme is disgusting, but you can make copies to rename
        self.resource_name = '{node_name}__{resource_name}'.format(
            node_name=self.node_name, resource_name=self.base_resource_name)

    def __str__(self):
        out = textwrap.dedent("""\
            [sub_resource type="{type}" id={i}]

            resource_name = "{resource_name}"
            length = 1.0
            loop = false
            step = 0.1
            tracks/0/type = "value"
            tracks/0/path = NodePath("{node_name}:playback/play")
            tracks/0/interp = 1
            tracks/0/loop_wrap = false
            tracks/0/imported = false
            tracks/0/enabled = true
            tracks/0/keys = {{
            "times": PoolRealArray( 0, 1 ),
            "transitions": PoolRealArray( 1, 1 ),
            "update": 1,
            "values": [ "{base_resource_name}", "[stop]" ]
            }}

            """.format(type=self.resource_type, i=self.i, resource_name=self.resource_name,
                       node_name=self.node_name, base_resource_name=self.base_resource_name))

        return out


class SubResourceSpineSkin(SubResourceSpine):
    """Create the animations for changing skins
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Slightly different naming from your basic animations
        self.resource_name = '{node_name}__skin__{resource_name}'.format(
            node_name=self.node_name, resource_name=self.base_resource_name)

        self.tracks = []

        # We add at least the one track that changes our skin, but in case
        # the `MainNode` we're creating has many `SpineNode`s, we will add
        # tracks later as well for them.
        self.add_track(node_name=self.node_name)

    def add_track(self, *, node_name):
        """Add track that affects `node_name` by setting the same base_resource_name
        in it. This is when you have multiple Spine nodes for movement with a character
        that can change costumes, because you want the costume to be the same in every
        direction.
        Note that this requires you to use the same names in every Spine json!
        """

        # This could be a class if we ever need generalization.
        # Note that these are 0-indexed so we can use len(self.tracks)
        track_data = """\
            tracks/{i}/type = "value"
            tracks/{i}/path = NodePath("{node_name}:playback/skin")
            tracks/{i}/interp = 1
            tracks/{i}/loop_wrap = true
            tracks/{i}/imported = false
            tracks/{i}/enabled = true
            tracks/{i}/keys = {{
            "times": PoolRealArray( 0 ),
            "transitions": PoolRealArray( 1 ),
            "update": 1,
            "values": [ "{base_resource_name}" ]
            }}
            """.format(i=len(self.tracks), node_name=node_name, base_resource_name=self.base_resource_name)

        self.tracks.append(textwrap.dedent(track_data))

    def __str__(self):
        out = textwrap.dedent("""\
            [sub_resource type="{type}" id={i}]

            resource_name = "{resource_name}"
            length = 0.01
            loop = false
            step = 0.1
            """.format(type=self.resource_type, i=self.i, resource_name=self.resource_name))

        for track_data in self.tracks:
            out = '{out}{track_data}'.format(out=out, track_data=track_data)

        out = '{out}\n'.format(out=out)

        return out


class MainNode:
    """This is the node we're creating, parent to the spine node(s) and AnimationPlayer
    """

    def __init__(self, *, name=None, node_type=None, parent=None, index=None, script=None, data=None):
        self.name = name
        if not getattr(self, 'node_type', None):
            self.node_type = node_type
        self.parent = parent
        self.index = index
        self.script = script

        self.load_steps = None

        self.ext_resources = []
        self.sub_resources = []
        self.children = []

        # Add a script if given
        if self.script is not None:
            self.add_ext_resource(resource_path=self.script, resource_type='Script')

    def __str__(self):
        out = ''

        # External resources are first
        for i, ext_resource in enumerate(self.ext_resources):
            ext_resource.i = i + 1
            out = '{out}{ext_resource}'.format(out=out, ext_resource=ext_resource)

        if out:
            out = '{out}\n'.format(out=out)

        # Then the sub-resources
        for i, sub_resource in enumerate(sorted(self.sub_resources, key=operator.attrgetter('resource_name'))):
            sub_resource.i = i + 1
            out = '{out}{sub_resource}'.format(out=out, sub_resource=sub_resource)

        # Deal with nodes
        if self.parent is None:
            out = textwrap.dedent("""\
                {out}
                [node name="{name}" type="{type}"]

                """.format(out=out,
                               name=self.name,
                               type=self.node_type
                           ))

        # The script is guaranteed to be the first external resource
        if self.script is not None:
            out = '{out}script = ExtResource( 1 )\n\n'.format(out=out)

        # Children are handled by the above formatting
        for child in self.children:
            out = '{out}{child}'.format(out=out, child=child)

        # After everything is set up for the main node, we can prepend a header
        if self.load_steps is not None:
            pre = '[gd_scene load_steps={load_steps} format=2]'.format(load_steps=self.load_steps)
            out = '{pre}\n\n{out}'.format(pre=pre, out=out.strip())

        return out

    def __bytes__(self):
        return str(self).encode('utf-8')

    def add_ext_resource(self, resource_path, resource_type):
        """Add the given file as an external resource
        """

        ext_resource = ExtResource(resource_path=resource_path, resource_type=resource_type)
        self.ext_resources.append(ext_resource)

    def add_extra_file(self, fname):
        """Read child data from fname and add it in self.children
        """

        with open(fname, 'r') as f:
            data = f.read()
        data = data.format(index=len(self.children))
        self.children.append(ExtraNode(data=data))

    def add_sub_resource(self, class_, resource_name, node_name):
        sub_resource = class_(resource_name=resource_name, node_name=node_name)
        self.sub_resources.append(sub_resource)

    def add_spine_child(self, json_file, index):
        """Look at json file and add its relevant contents.
        These indexes start from 0
        """

        with open(json_file, 'r') as raw_json:
            self.add_ext_resource(json_file, 'SpineResource')

            json_data = json.load(raw_json)
            base_name = os.path.basename(json_file).replace('.json', '').replace('.', '_')
            node_name = 'spine_{}'.format(base_name)

            child = SpineNode(name=node_name, parent='.', index=index)
            child.ext_resource_i = len(self.ext_resources)
            self.children.append(child)

            for animation_name in json_data['animations'].keys():
                self.add_sub_resource(SubResourceSpinePlay, animation_name, node_name)

            for skin_name in json_data['skins'].keys():
                self.add_sub_resource(SubResourceSpineSkin, skin_name, node_name)

    def add_animation_player(self):
        """Make sure we have an animation player too, unless we're a child
        """

        assert self.parent is None
        index = len(self.children)  # 0-indexed, remember
        self.animation_player = AnimationPlayerNode(name='animation', parent='.', index=index)
        self.children.append(self.animation_player)

    def fix_spine_sub_resources(self):
        """Add the relevant skin changes, if any, and tie Spine animations to AnimationPlayerNode
        """

        # Go through all the skin nodes for all skins and make sure they all are changed.
        # If one node contains more skins than another, this will create useless animations.
        spine_skins = [sr for sr in self.sub_resources if isinstance(sr, SubResourceSpineSkin)]
        nodes_with_skins = set(skin.node_name for skin in spine_skins)
        for skin in spine_skins:
            for node_name in nodes_with_skins:
                # We already have ourselves as the default track so skip it
                if skin.node_name != node_name:
                    skin.add_track(node_name=node_name)

        for i, sub_resource in enumerate(sorted(self.sub_resources, key=operator.attrgetter('resource_name'))):
            if isinstance(sub_resource, SubResourceAnimation):
                # And sub-resources are 1-indexed
                self.animation_player.animations.append((i + 1, sub_resource))

    def set_load_steps(self):
        """Once everything is in order, we need the header part
        """

        # The last 1 is our self
        self.load_steps = len(self.ext_resources) + len(self.sub_resources) + 1


class ExtraNode(MainNode):
    """Whatever pre-existing extra data you want
    """

    def __init__(self, data):
        self.data = data

    def __str__(self):
        return self.data


class SpineNode(MainNode):
    """To add Spine nodes
    """

    node_type = 'Spine'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Reference to ExtResource number
        self.ext_resource_i = None

    def __str__(self):
        out = textwrap.dedent("""\
            [node name="{name}" type="{type}" parent="{parent}" index="{index}"]

            process_mode = 1
            speed = 1.0
            active = true
            skip_frames = 0
            debug_bones = false
            flip_x = false
            flip_y = false
            fx_prefix = "fx/"
            resource = ExtResource( {ext_resource_i} )
            playback/play = "[stop]"
            playback/loop = true
            playback/forward = true
            playback/skin = "default"
            debug/region = false
            debug/mesh = false
            debug/skinned_mesh = false
            debug/bounding_box = false
            _sections_unfolded = [ "playback", "Transform", "Visibility" ]

            """.format(name=self.name,
                       type=self.node_type,
                       parent=self.parent,
                       index=self.index,
                       ext_resource_i=self.ext_resource_i
                       ))

        return out


class AnimationPlayerNode(MainNode):
    """Appended as the last node with ties to relevant SubResources
    """

    node_type = 'AnimationPlayer'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Keep the named animations here
        self.animations = []

    def __str__(self):
        out = textwrap.dedent("""\
            [node name="{name}" type="{type}" parent="{parent}" index="{index}"]

            """.format(name=self.name,
                       type=self.node_type,
                       parent=self.parent,
                       index=self.index
                       ))

        out = textwrap.dedent("""\
            {out}
            root_node = NodePath("..")
            autoplay = ""
            playback_process_mode = 1
            playback_default_blend_time = 0.0
            playback_speed = 1.0
            """.format(out=out))

        for i, sr in self.animations:
            out = '{out}anims/{name} = SubResource( {i} )\n'.format(out=out, name=sr.resource_name, i=i)

        out = '{out}blend_times = [  ]\n'.format(out=out)

        return out


def build_tree(args):
    main_node = MainNode(name=args['-n'], node_type=args['-t'], script=args['-s'])

    for i, json_file in enumerate(args['<json>']):
        main_node.add_spine_child(json_file, i)

    if args['-e']:
        main_node.add_extra_file(args['-e'])

    main_node.add_animation_player()

    main_node.fix_spine_sub_resources()

    main_node.set_load_steps()

    return main_node


def main(args):
    if not os.path.exists('project.godot'):
        print('Run this in your project root so paths are resolved correctly.')
        return 1

    tree = build_tree(args)

    print(str(tree))

    return 0


if __name__ == '__main__':
    sys.exit(main(docopt(doc, sys.argv[1:])))
