"""Latin-only Unicode font variants for the public ``فونت`` command.

Persian text, digits, punctuation, and spacing are intentionally preserved
unless the selected style defines a Unicode full-width ASCII conversion.
"""
from __future__ import annotations

import string

_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_ASCII = _LOWER + _UPPER


def _shifted(text: str, lower_start: int, upper_start: int) -> str:
    table = str.maketrans(
        _ASCII,
        "".join(chr(lower_start + i) for i in range(26))
        + "".join(chr(upper_start + i) for i in range(26)),
    )
    return text.translate(table)


def _mapped(text: str, lower: str, upper: str) -> str:
    return text.translate(str.maketrans(_ASCII, lower + upper))


def _bold(text: str) -> str:
    return _shifted(text, 0x1D41A, 0x1D400)


def _monospace(text: str) -> str:
    return _shifted(text, 0x1D68A, 0x1D670)


def _italic(text: str) -> str:
    return _mapped(
        text,
        "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻",
        "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡",
    )


def _circled(text: str) -> str:
    return _mapped(
        text,
        "ⓐⓑⓒⓓⓔⓕⓖⓗⓘⓙⓚⓛⓜⓝⓞⓟⓠⓡⓢⓣⓤⓥⓦⓧⓨⓩ",
        "ⒶⒷⒸⒹⒺⒻⒼⒽⒾⒿⓀⓁⓂⓃⓄⓅⓆⓇⓈⓉⓊⓋⓌⓍⓎⓏ",
    )


def _full_width(text: str) -> str:
    return "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in text)


def _upside_down(text: str) -> str:
    return _mapped(
        text.lower(),
        "ɐqɔpǝɟƃɥᴉɾʞןɯuodbɹsʇnʌʍxʎz",
        "ɐqɔpǝɟƃɥᴉɾʞןɯuodbɹsʇnʌʍxʎz",
    )


def _small_caps(text: str) -> str:
    return _mapped(
        text.lower(),
        "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
        "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
    )


def _double_struck(text: str) -> str:
    return _mapped(
        text,
        "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫",
        _UPPER,
    )


def _math_italic(text: str) -> str:
    return _mapped(
        text,
        "𝑎𝑏𝑐𝑑𝑒𝑓𝑔ℎ𝑖𝑗𝑘𝑙𝑚𝑛𝑜𝑝𝑞𝑟𝑠𝑡𝑢𝑣𝑤𝑥𝑦𝑧",
        "𝐴𝐵𝐶𝐷𝐸𝐹𝐺𝐻𝐼𝐽𝐾𝐿𝑀𝑁𝑂𝑃𝑄𝑅𝑆𝑇𝑈𝑉𝑊𝑋𝑌𝑍",
    )


def _math_bold_italic(text: str) -> str:
    return _mapped(
        text,
        "𝒂𝒃𝒄𝒅𝒆𝒇𝒈𝒉𝒊𝒋𝒌𝒍𝒎𝒏𝒐𝒑𝒒𝒓𝒔𝒕𝒖𝒗𝒘𝒙𝒚𝒛",
        "𝑨𝑩𝑪𝑫𝑬𝑭𝑮𝑯𝑰𝑱𝑲𝑳𝑴𝑵𝑶𝑷𝑸𝑹𝑺𝑻𝑼𝑽𝑾𝑿𝒀𝒁",
    )


def _script(text: str) -> str:
    return _mapped(
        text,
        "𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃",
        "𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩",
    )


def _fraktur(text: str) -> str:
    return _mapped(
        text,
        "𝔞𝔟𝔠𝔡𝔢𝔣𝔤𝔥𝔦𝔧𝔨𝔩𝔪𝔫𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷",
        "𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ",
    )


def _bold_fraktur(text: str) -> str:
    return _mapped(
        text,
        "𝖆𝖇𝖈𝖉𝖊𝖋𝖌𝖍𝖎𝖏𝖐𝖑𝖒𝖓𝖔𝖕𝖖𝖗𝖘𝖙𝖚𝖛𝖜𝖝𝖞𝖟",
        "𝕬𝕭𝕮𝕯𝕰𝕱𝕲𝕳𝕴𝕵𝕶𝕷𝕸𝕹𝕺𝕻𝕼𝕽𝕾𝕿𝖀𝖁𝖂𝖃𝖄𝖅",
    )


def _sans_bold_italic(text: str) -> str:
    return _mapped(
        text,
        "𝙖𝙗𝙘𝙙𝙚𝙛𝙜𝙝𝙞𝙟𝙠𝙡𝙢𝙣𝙤𝙥𝙦𝙧𝙨𝙩𝙪𝙫𝙬𝙭𝙮𝙯",
        "𝘼𝘽𝘾𝘿𝙀𝙁𝙂𝙃𝙄𝙅𝙆𝙇𝙈𝙉𝙊𝙋𝙌𝙍𝙎𝙏𝙐𝙑𝙒𝙓𝙔𝙕",
    )


def make_fonts(text: str) -> list[str]:
    """Return fourteen Unicode styles for Latin letters in ``text``."""
    return [
        _bold(text),
        _monospace(text),
        _italic(text),
        _circled(text),
        _full_width(text),
        _upside_down(text),
        _small_caps(text),
        _double_struck(text),
        _math_italic(text),
        _math_bold_italic(text),
        _script(text),
        _fraktur(text),
        _bold_fraktur(text),
        _sans_bold_italic(text),
    ]
