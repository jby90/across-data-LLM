# ruff: noqa: E501
"""Which products an admission item is about, for the “产品类别” filter of the admission entry
(origin, destination and product, as trade assistants ask for them).

One rule for every country, by the item's category and the words of its name:
- an approval whose object, as the item's name says, is a device (equipment, terminal, module,
  radio installation, labelled IoT device, encryption means, security module) is about the on-board
  communication module, not about a vehicle type; a licence to run a network or a service, or to use
  a frequency, is the operator's and stays with every product;
- a vehicle approval is about vehicles; when its name names a commercial class (truck, bus,
  trailer, N/M2/M3/O) or a passenger class (M1, passenger car) only that class, else both;
- an autonomous-driving test or operation permit is about vehicles (both classes);
- everything else (data processing, transfer, operating a network or service, encryption…) and
  any item the rule cannot place applies whatever the product is.
"""

from __future__ import annotations

import re

PRODUCTS = {"passenger": "乘用车", "commercial": "商用车", "module": "车载通信模块"}
VEHICLES = ["passenger", "commercial"]
EVERY = list(PRODUCTS)

_DEVICE = re.compile(r"设备|终端|模块|无线电设施|标签|加密手段")
_OPERATOR = re.compile(r"网络|服务|频率|频谱")
_COMMERCIAL = re.compile(r"商用|货车|载货|客车|大客|公交|挂车|重型|N[1-3]类?|M[23]类?|O[1-4]类")
_PASSENGER = re.compile(r"乘用|轿车|M1类?|小客车")


def products_of(category: str, name: str) -> list[str]:
    name = name or ""
    if category in ("telecom_approval", "other"):
        # “网络安全标签”是设备的安全认证，不是经营网络
        return ["module"] if _DEVICE.search(name) and not _OPERATOR.search(name.replace("网络安全", "")) else EVERY
    if category == "vehicle_approval":
        out = [k for k, pat in (("passenger", _PASSENGER), ("commercial", _COMMERCIAL)) if pat.search(name)]
        return out or VEHICLES
    if category == "av_testing":
        return VEHICLES
    return EVERY


def fits(product: str, category: str, name: str) -> bool:
    """Whether the item is shown when the user picked this product (none picked: every item)."""
    return not product or product in products_of(category, name)


def label(keys: list[str]) -> str:
    return "各类车辆" if keys == EVERY else "、".join(PRODUCTS[k] for k in keys)
