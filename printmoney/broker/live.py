"""Live broker. Off by default, and it stays off until four separate gates agree.

Nothing in this file runs unless the operator has, on their own machine:

1. set ``execution.mode: live`` in config.yaml,
2. set ``live.enabled: true``,
3. typed the confirmation phrase into ``live.confirm_phrase``, and
4. exported their own private key into the environment.

The key is read from the environment at the moment of use and never logged,
never written to disk, and never passed anywhere except the signing client.
``py-clob-client`` is imported lazily so the rest of the project works without it
installed - which is the normal state of affairs, because the normal state of
affairs is paper trading.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import Config
from ..ledger import Fill, Ledger
from ..util import fmt_usd, utcnow
from . import ExecOrder

log = logging.getLogger("printmoney.live")


class LiveBrokerError(RuntimeError):
    pass


@dataclass
class LiveBroker:
    cfg: Config
    mode: str = "live"
    _client: Any = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        armed, why = self.cfg.live_armed()
        return f"live broker ({'ARMED' if armed else 'disarmed: ' + why})"

    def _require_armed(self) -> None:
        armed, why = self.cfg.live_armed()
        if not armed:
            raise LiveBrokerError(f"refusing to trade live: {why}")

    # ------------------------------------------------------------------ #
    def client(self) -> Any:
        """Build the signing client on first use."""
        if self._client is not None:
            return self._client
        self._require_armed()

        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import ApiCreds  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on operator setup
            raise LiveBrokerError(
                "py-clob-client is not installed. Run: pip install py-clob-client"
            ) from exc

        lc = self.cfg.live
        key = os.environ.get(lc.private_key_env, "").strip()
        if not key:
            raise LiveBrokerError(f"{lc.private_key_env} is empty")

        funder = os.environ.get(lc.funder_address_env, "").strip() or None
        client = ClobClient(
            host=lc.host,
            key=key,
            chain_id=lc.chain_id,
            signature_type=lc.signature_type,
            funder=funder,
        )

        api_key = os.environ.get(lc.api_key_env, "").strip()
        api_secret = os.environ.get(lc.api_secret_env, "").strip()
        api_pass = os.environ.get(lc.api_passphrase_env, "").strip()
        if api_key and api_secret and api_pass:
            client.set_api_creds(
                ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass)
            )
        else:
            log.info("no stored API creds found; deriving them from the signing key")
            client.set_api_creds(client.create_or_derive_api_creds())

        self._client = client
        log.warning("LIVE trading client initialised against %s", lc.host)
        return client

    # ------------------------------------------------------------------ #
    def execute(self, orders: Sequence[ExecOrder], ledger: Ledger) -> list[Fill]:
        self._require_armed()
        client = self.client()

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise LiveBrokerError("py-clob-client is not installed") from exc

        fills: list[Fill] = []
        for order in orders:
            price = _round_to_tick(order.price, order.tick_size)
            args = OrderArgs(
                token_id=order.token_id,
                price=price,
                size=round(order.shares, 2),
                side="BUY",
            )
            try:
                signed = client.create_order(args)
                # FOK: either we get the size we planned around or we get nothing.
                # A partial fill would break the hedge the LP just solved for.
                resp = client.post_order(signed, OrderType.FOK)
            except Exception as exc:  # noqa: BLE001
                log.error("live order rejected (%s %s): %s", order.leg_label, order.side, exc)
                continue

            filled = _filled_size(resp, order.shares)
            if filled <= 0:
                log.warning("live order not filled: %s %s", order.leg_label, order.side)
                continue

            fill = Fill(
                ts=utcnow(),
                token_id=order.token_id,
                strip_slug=order.strip_slug,
                leg_label=order.leg_label,
                question=order.question,
                side=order.side,
                price=price,
                shares=filled,
                fee=filled * order.fee_per_share,
                mode="live",
                order_id=str((resp or {}).get("orderID") or (resp or {}).get("orderId") or ""),
            )
            ledger.record_fill(fill, expiry=order.expiry)
            fills.append(fill)
            log.warning(
                "LIVE fill: %s %s %.2f @ %.3f (%s)",
                order.leg_label,
                order.side,
                filled,
                price,
                fmt_usd(fill.cost),
            )
        return fills

    # ------------------------------------------------------------------ #
    def cancel_all(self) -> None:
        self._require_armed()
        try:
            self.client().cancel_all()
            log.warning("cancelled all resting live orders")
        except Exception as exc:  # noqa: BLE001
            log.error("cancel_all failed: %s", exc)


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 3)
    steps = round(price / tick)
    return round(steps * tick, 6)


def _filled_size(resp: Any, requested: float) -> float:
    """Read the filled size out of whatever shape the API returned."""
    if not isinstance(resp, dict):
        return 0.0
    if resp.get("success") is False:
        return 0.0
    for key in ("takingAmount", "size_matched", "sizeMatched", "filled"):
        val = resp.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    status = str(resp.get("status", "")).lower()
    if status in ("matched", "filled"):
        return float(requested)
    return 0.0
