"""What the free addresses are made of: two plain words, maple-river.

Random, never chosen: every certificate is in public logs within minutes, so a name must not tell who the family is,
and a name nobody picks can't be made to look like a bank's. Two words and nothing else, so a grandparent can read one
out on the phone (the user's call, 2026-11-24: a number was hard to say). So the words are easy to say and to spell:
none that sound like another word (bear, bare), none spelled two ways (harbor, harbour), no brand names. Two lists of
about 160 words: over 25,000 names. Names given before (two words and a number, maple-river-42) keep working.
"""
import secrets

FIRST = """
alder almond amber apricot aspen autumn barley basil birch bramble breezy bright bronze butter calm canyon cedar
cherry cinnamon citrus clear cloudy clover cobalt cocoa comet copper coral cotton cranberry crimson crisp crystal
cypress daisy dawn desert dusk eastern elm ember evergreen feather fern field firefly foggy forest frosty garnet
gentle ginger glacier golden granite green hazel heather hickory hidden holly honey indigo iris island ivy jade
jasmine jolly juniper kindly lake laurel lavender lemon lilac linen lofty lucky lunar magnolia maple marble
marigold meadow mellow merry midnight mighty mild misty moss mulberry north nutmeg oak ocean olive opal orchard
peach pearl pebble pepper pine polar poppy prairie purple quiet rainbow ridge river robin rosy ruby rustic saffron
sage sandy scarlet shady sierra silver sky slate snowy soft south spice spring spruce starry sterling still stone
sugar summer sunny sunset sweet swift tall tawny thistle thunder timber topaz tulip twilight upland valley velvet
violet walnut warm west wheat willow windy winter wild woodland wren yellow young
""".split()

SECOND = """
acorn anchor arrow aspen badger barn basin basket bay beach beacon bench birch blossom bluff boat bonfire branch
bridge brook burrow butterfly cabin cactus candle canoe canyon castle cedar chapel cherry chimney cliff clover
comet cottage cove crest cricket daisy dock dove dragonfly dune eagle falcon farm feather fern fiddle field finch
fireside forest fountain fox garden gate glade glen grove gull harvest haven hawk hearth heron hill hollow honey
horizon inlet island jay juniper kettle kite ladder lagoon lake lantern lark leaf ledge lily lodge maple marsh
meadow mesa mill mitten moon mountain nest oak orchard otter owl paddle path pebble pine pond pony porch prairie
puffin quilt rabbit ranch raven reef ridge river robin rock sailboat seashell shore sky sled sparrow spring sprout
star stream summit sunflower swan teapot thicket timber trail tree trout tulip tunnel turtle valley village vine
violet wagon walnut waterfall wharf whistle willow window wren
""".split()


def valid(name: str) -> bool:
    """Whether name is one new_name could make: a name chosen from the suggestions is still two of these words."""
    first, _, second = name.partition("-")
    return first in FIRST and second in SECOND and first != second


def new_name() -> str:
    """maple-river: two different words."""
    first = secrets.choice(FIRST)
    second = secrets.choice([w for w in SECOND if w != first])
    return f"{first}-{second}"
