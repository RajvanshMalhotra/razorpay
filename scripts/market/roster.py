"""Thirty-two merchants, with a trend planted in the data.

WHY A TREND IS PLANTED AT ALL, since this is the one design choice here worth
arguing about.

The house research agent's entire value is finding a cross-merchant pattern no
single merchant can see. Give thirty merchants unrelated random needs and there
is no such pattern in the market — so the agent either reports nothing, which
is a boring demo, or invents one, which is a dishonest demo. Neither is worth
recording.

So the roster encodes real structure: a cluster of Bangalore beverage merchants
whose demand for cold brew concentrate genuinely grows across four rounds,
suppliers who serve them at different prices and reliabilities, and merchants
in four other categories trading among themselves so the signal has noise to be
found in rather than being the only thing present.

**We plant the cause, never the conclusion.** The house agent is told nothing.
It has no privileged access, reads the same aggregate settled activity every
other reader sees, and has to notice. `demand_by_round` exists so the trend can
be measured out of the roster itself — that is what makes it real rather than
claimed, and `tests/test_roster.py` asserts it rises rather than asserting that
we said it does.

A market where nothing correlates is not more honest than one where something
does. It is a market with no news in it, and real markets have news.

PRICES ARE PAISE PER UNIT, integers throughout. Personas are prompt text, never
weights: the agent reasons from them, nothing multiplies by them.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Listing:
    asset_id: str
    title: str
    spec: dict
    ask_price: int   # paise per unit
    qty: int


@dataclass(frozen=True)
class Need:
    round_no: int
    text: str
    qty: int
    limit_price: int  # paise per unit


@dataclass(frozen=True)
class Merchant:
    actor_id: str
    name: str
    category: str
    persona: str
    sells: tuple[Listing, ...]
    needs: tuple[Need, ...]
    # Shorthand from `exchange.agents.mandate.KEYWORDS`. The persona is this
    # merchant's own words; the keywords are the same intent in a form the
    # roles already share a vocabulary for. Both reach the agent.
    style: str = ""

    def mandate_input(self) -> str:
        """What this merchant would have typed into the box."""
        return ", ".join(part for part in (self.style, self.persona) if part)


def demand_by_round(input_text: str) -> dict[int, int]:
    """Total quantity sought for an input, per round, across every merchant.

    The planted trend has to be measurable from the roster itself. If this
    does not rise, the house agent has nothing true to find.
    """
    totals: dict[int, int] = {}
    needle = input_text.lower()
    for merchant in MERCHANTS:
        for need in merchant.needs:
            if needle in need.text.lower():
                totals[need.round_no] = totals.get(need.round_no, 0) + need.qty
    return totals


# --- the suppliers the cluster buys from ------------------------------------
#
# Three prices and three stories. `choose()` has a real decision to make and
# the Diplomat's read on each counterparty is what breaks the tie — which is
# only interesting because the cheapest is not obviously the best.

_SUPPLIERS = (
    Merchant(
        actor_id="m_kaapi_roasters", style="persistent, quality_first",
        name="Kaapi Roasters",
        category="coffee_supply",
        persona="Holds price firmly and points to consistency; will not discount below 5%.",
        sells=(
            Listing("ast_cb_kaapi", "cold brew concentrate arabica single origin",
                    {"roast": "medium", "origin": "Chikmagalur"}, 34000, 6000),
            Listing("ast_beans_kaapi", "green coffee beans washed arabica",
                    {"grade": "AA"}, 21000, 4000),
        ),
        needs=(
            Need(2, "biodegradable mailers for subscription boxes", 400, 2300),
            Need(4, "corrugated cartons for bulk despatch", 300, 4200),
        ),
    ),
    Merchant(
        actor_id="m_western_ghats", style="patient, fair",
        name="Western Ghats Coffee Co",
        category="coffee_supply",
        persona="Opens high, concedes steadily, and always asks for a longer commitment.",
        sells=(
            Listing("ast_cb_ghats", "cold brew concentrate robusta blend bulk",
                    {"roast": "dark", "origin": "Coorg"}, 29500, 9000),
        ),
        needs=(
            Need(1, "food grade PET bottles one litre", 900, 1800),
            Need(3, "shrink wrap film for pallet loads", 500, 900),
        ),
    ),
    Merchant(
        actor_id="m_nilgiri_cold", style="aggressive, price_first",
        name="Nilgiri Cold Extracts",
        category="coffee_supply",
        persona="Cheapest in the market and knows it; slow on delivery and evasive about dates.",
        sells=(
            Listing("ast_cb_nilgiri", "cold brew concentrate value grade",
                    {"roast": "medium-dark", "origin": "Nilgiris"}, 24500, 12000),
        ),
        needs=(
            Need(2, "food grade PET bottles one litre", 1200, 1900),
        ),
    ),
    Merchant(
        actor_id="m_third_wave_supply", style="delivery_first, loyal",
        name="Third Wave Supply",
        category="coffee_supply",
        persona="Sells on reliability rather than price and documents every delivery window.",
        sells=(
            Listing("ast_cb_thirdwave", "cold brew concentrate speciality nitro ready",
                    {"roast": "light", "origin": "Araku"}, 38000, 3500),
        ),
        needs=(
            Need(3, "biodegradable mailers for subscription boxes", 250, 2400),
        ),
    ),
)


# --- the demand cluster -----------------------------------------------------
#
# Nine Bangalore beverage merchants. Their need for cold brew concentrate grows
# 400 -> 900 -> 1600 -> 2600 across four rounds, and the wording differs from
# merchant to merchant so retrieval has to do real work rather than match a
# fixed string.

# One flavour each, so nine cafes are a market rather than nine copies.
SYRUPS = ("vanilla bean", "hazelnut", "salted caramel", "cardamom",
          "cinnamon", "dark chocolate", "gingerbread", "toasted coconut",
          "rose")


def _cluster() -> tuple[Merchant, ...]:
    # Style shorthand per merchant, so nine merchants in one category still
    # brief their agents differently — otherwise the cluster reads as one
    # buyer with nine names.
    styles = [
        "aggressive, walk_early", "decisive, delivery_first",
        "fair, decisive", "aggressive, price_first",
        "delivery_first, patient", "persistent, fair",
        "price_first, walk_early", "patient, price_first",
        "cautious, explorer",
    ]
    specs = [
        ("m_bl_thirdwave", "Third Wave Bengaluru", "Walks away rather than overpay; treats the first ask as an opening.",
         "cold brew concentrate for a twelve store rollout, unsweetened"),
        ("m_bl_koramangala", "Koramangala Coffee House", "Friendly and fast to agree, but pushes hard on delivery dates.",
         "unsweetened cold brew concentrate, cafe grade, weekly cadence"),
        ("m_bl_indiranagar", "Indiranagar Brew Bar", "Splits the difference on almost anything; dislikes long haggles.",
         "cold brew concentrate in bulk for nitro taps"),
        ("m_bl_whitefield", "Whitefield Roastery", "Opens low and concedes slowly; quotes competitors by name.",
         "bulk cold brew concentrate, arabica preferred, low acidity"),
        ("m_bl_jayanagar", "Jayanagar Filter Room", "Values delivery certainty over price and says so plainly.",
         "cold brew concentrate for iced menu, consistent batch to batch"),
        ("m_bl_hsr", "HSR Daily Grind", "Negotiates in small increments and rarely walks.",
         "cold brew concentrate, ready to dilute, food safe packaging"),
        ("m_bl_malleswaram", "Malleswaram Coffee Works", "Blunt about budget; states a ceiling early and holds it.",
         "cold brew concentrate at volume for a summer menu"),
        ("m_bl_electronic_city", "Electronic City Cafe Group", "Buys on total cost including freight, not unit price.",
         "cold brew concentrate delivered, cafe grade, monthly volume"),
        ("m_bl_yelahanka", "Yelahanka Brewhouse", "Cautious with new suppliers; starts small and scales on trust.",
         "cold brew concentrate trial quantity, arabica single origin"),
    ]
    # 400 / 900 / 1600 / 2600 — the trend, distributed unevenly so it reads
    # like a market rather than an arithmetic sequence.
    schedule = {
        1: [100, 120, 90, 90],
        2: [160, 150, 140, 150, 150, 150],
        3: [200, 210, 190, 200, 200, 200, 200, 200],
        4: [300, 290, 280, 290, 290, 290, 290, 290, 280],
    }
    merchants = []
    for index, (actor_id, name, persona, need_text) in enumerate(specs):
        needs = []
        for round_no, quantities in schedule.items():
            if index < len(quantities):
                needs.append(Need(
                    round_no=round_no,
                    text=need_text,
                    qty=quantities[index],
                    # Above the cheapest supplier, below the dearest, so the
                    # choice is genuinely open and some merchants price
                    # themselves out of the speciality tier.
                    limit_price=31000 + (index % 4) * 2500,
                ))
        merchants.append(Merchant(
            actor_id=actor_id,
            name=name,
            category="beverage",
            persona=persona,
            style=styles[index % len(styles)],
            # A LOOP COUNTER IS NOT A PRODUCT NAME. This read "flavour syrup
            # bottles house blend 0" on the shop window of a real-looking
            # cafe, which is the index leaking into the thing a customer
            # sees. Nine cafes selling nine flavours is also a better market
            # than nine selling the same bottle.
            sells=(
                Listing(f"ast_syrup_{actor_id[5:]}",
                        f"{SYRUPS[index % len(SYRUPS)]} syrup bottles",
                        {"flavour": SYRUPS[index % len(SYRUPS)]},
                        12000 + index * 300, 500),
            ),
            needs=tuple(needs),
        ))
    return tuple(merchants)


# --- the rest of the market -------------------------------------------------
#
# Four other categories, trading among themselves. Without them the cluster is
# the only thing in the log and "cold brew is rising in Bangalore" is not a
# discovery, it is the only sentence available.

_PACKAGING = (
    Merchant(
        actor_id="m_greenwrap", style="fair, decisive", name="GreenWrap Packaging", category="packaging",
        persona="Quotes once and means it; explains cost rather than defending it.",
        sells=(
            Listing("ast_mailers_green", "biodegradable mailers compostable poly 10x13",
                    {"material": "compostable poly"}, 1940, 20000),
            Listing("ast_cartons_green", "corrugated cartons double wall",
                    {"wall": "double"}, 3800, 8000),
        ),
        needs=(Need(1, "recycled kraft paper reels", 600, 5200),),
    ),
    Merchant(
        actor_id="m_packmate", style="aggressive, decisive, price_first", name="PackMate Industries", category="packaging",
        persona="Aggressive on price, vague on lead times, quick to close.",
        sells=(
            Listing("ast_mailers_pack", "biodegradable mailers for subscription boxes",
                    {"material": "kraft"}, 2100, 15000),
            Listing("ast_shrink_pack", "shrink wrap film for pallet loads",
                    {"microns": 23}, 850, 9000),
        ),
        needs=(Need(2, "recycled kraft paper reels", 900, 5400),),
    ),
    Merchant(
        actor_id="m_bottleworks", style="patient, quality_first", name="BottleWorks", category="packaging",
        persona="Methodical, asks clarifying questions before quoting anything.",
        sells=(
            Listing("ast_pet_bottle", "food grade PET bottles one litre",
                    {"neck": "38mm"}, 1750, 40000),
        ),
        needs=(Need(3, "shrink wrap film for pallet loads", 400, 950),),
    ),
    Merchant(
        actor_id="m_reelco", style="fair, persistent", name="ReelCo Paper", category="packaging",
        persona="Long-winded but honest; volunteers when it cannot deliver.",
        sells=(
            Listing("ast_kraft_reel", "recycled kraft paper reels 120gsm",
                    {"gsm": 120}, 5000, 6000),
        ),
        needs=(Need(4, "corrugated cartons double wall", 200, 4000),),
    ),
    Merchant(
        actor_id="m_labelhouse", style="decisive, explorer", name="LabelHouse", category="packaging",
        persona="Small operator, eager for a first order, discounts to win one.",
        sells=(
            Listing("ast_labels", "waterproof product labels roll fed",
                    {"adhesive": "permanent"}, 600, 50000),
        ),
        needs=(Need(2, "recycled kraft paper reels", 300, 5300),),
    ),
    Merchant(
        actor_id="m_cratehub", style="patient, price_first", name="CrateHub Logistics", category="packaging",
        persona="Talks in totals rather than unit prices; hard to pin down early.",
        sells=(
            Listing("ast_crates", "returnable plastic crates stackable",
                    {"litres": 40}, 42000, 900),
        ),
        needs=(Need(3, "waterproof product labels roll fed", 8000, 700),),
    ),
)

_TEXTILES = (
    Merchant(
        actor_id="m_loomline", style="patient, persistent, quality_first", name="LoomLine Textiles", category="textiles",
        persona="Formal and slow; will not move price without a volume increase.",
        sells=(Listing("ast_cotton_twill", "organic cotton twill fabric rolls",
                       {"gsm": 240}, 28000, 3000),),
        needs=(Need(1, "reactive dye pigments industrial", 200, 9000),),
    ),
    Merchant(
        actor_id="m_dyeworks", style="fair, quality_first", name="DyeWorks Chemicals", category="textiles",
        persona="Technical, precise, and unmoved by urgency.",
        sells=(Listing("ast_dye", "reactive dye pigments industrial grade",
                       {"fastness": "high"}, 8600, 2500),),
        needs=(Need(2, "organic cotton twill fabric rolls", 150, 30000),),
    ),
    Merchant(
        actor_id="m_stitchright", style="decisive, price_first", name="StitchRight Apparel", category="textiles",
        persona="Impatient; accepts a fair price quickly to save time.",
        sells=(Listing("ast_tshirt_blank", "blank cotton tshirts combed ringspun",
                       {"gsm": 180}, 18000, 5000),),
        needs=(Need(2, "organic cotton twill fabric rolls", 300, 29500),
               Need(4, "waterproof product labels roll fed", 12000, 650)),
    ),
    Merchant(
        actor_id="m_weavehouse", style="loyal, patient", name="WeaveHouse", category="textiles",
        persona="Relationship-first; references past dealings constantly.",
        sells=(Listing("ast_linen", "linen blend fabric rolls natural",
                       {"blend": "55/45"}, 34000, 1800),),
        needs=(Need(3, "reactive dye pigments industrial", 180, 9200),),
    ),
    Merchant(
        actor_id="m_threadbare", style="explorer, decisive", name="ThreadBare Studio", category="textiles",
        persona="New and unproven; over-promises slightly and knows it.",
        sells=(Listing("ast_thread", "polyester thread cones industrial",
                       {"tex": 40}, 900, 20000),),
        needs=(Need(1, "blank cotton tshirts combed ringspun", 400, 19500),),
    ),
)

_ELECTRONICS = (
    Merchant(
        actor_id="m_cablecraft", style="decisive, price_first", name="CableCraft", category="electronics_accessories",
        persona="Direct and transactional; no small talk, fast decisions.",
        sells=(Listing("ast_usbc", "braided USB C cables one metre",
                       {"rating": "60W"}, 14000, 8000),),
        needs=(Need(2, "injection moulded ABS enclosures", 1500, 5500),),
    ),
    Merchant(
        actor_id="m_mouldtech", style="fair, quality_first", name="MouldTech", category="electronics_accessories",
        persona="Engineering-minded; explains constraints rather than refusing.",
        sells=(Listing("ast_abs", "injection moulded ABS enclosures small",
                       {"finish": "matte"}, 5200, 12000),),
        needs=(Need(3, "braided USB C cables one metre", 600, 15000),),
    ),
    Merchant(
        actor_id="m_powerbank", style="aggressive, loyal", name="PowerBank Assemblers", category="electronics_accessories",
        persona="Bargains hard on the first order and loyally afterwards.",
        sells=(Listing("ast_cells", "lithium cells 18650 grade A",
                       {"mah": 2600}, 21000, 9000),),
        needs=(Need(1, "injection moulded ABS enclosures", 2000, 5400),
               Need(4, "braided USB C cables one metre", 900, 14500)),
    ),
    Merchant(
        actor_id="m_screenguard", style="cautious, delivery_first", name="ScreenGuard Co", category="electronics_accessories",
        persona="Cautious with unknown sellers; insists on a trial quantity first.",
        sells=(Listing("ast_tempered", "tempered glass screen protectors bulk",
                       {"hardness": "9H"}, 3400, 30000),),
        needs=(Need(3, "lithium cells 18650 grade A", 500, 22000),),
    ),
)

_DRY_GOODS = (
    Merchant(
        actor_id="m_spicebazaar", style="patient, fair", name="Spice Bazaar Wholesale", category="dry_goods",
        persona="Warm and talkative; concedes to people who engage rather than push.",
        sells=(Listing("ast_cardamom", "green cardamom whole pods bulk",
                       {"grade": "8mm"}, 180000, 400),),
        needs=(Need(2, "jute sacks hessian lined", 2000, 4200),),
    ),
    Merchant(
        actor_id="m_jutemill", style="decisive, walk_early", name="JuteMill Exports", category="dry_goods",
        persona="Terse. Quotes a number and waits.",
        sells=(Listing("ast_jute", "jute sacks hessian lined",
                       {"litres": 50}, 4000, 15000),),
        needs=(Need(3, "polyester thread cones industrial", 800, 950),),
    ),
    Merchant(
        actor_id="m_millstone", style="cautious, delivery_first", name="Millstone Foods", category="dry_goods",
        persona="Risk-averse; asks about delivery guarantees before price.",
        sells=(Listing("ast_flour", "stoneground wheat flour sacks",
                       {"kg": 25}, 92000, 1200),),
        needs=(Need(1, "jute sacks hessian lined", 1500, 4300),
               Need(4, "green cardamom whole pods bulk", 60, 190000)),
    ),
    Merchant(
        actor_id="m_saltworks", style="aggressive, walk_early", name="SaltWorks Trading", category="dry_goods",
        persona="Opportunistic; revisits agreed terms if the market moves.",
        sells=(Listing("ast_salt", "iodised salt bulk edible",
                       {"kg": 50}, 38000, 5000),),
        needs=(Need(4, "jute sacks hessian lined", 1000, 4100),),
    ),
)


def _give_everyone_a_second_chance(
    merchants: tuple[Merchant, ...],
) -> tuple[Merchant, ...]:
    """Nobody buys only once in a whole market.

    A first run left 12 of 32 merchants having never traded, and 10 of them
    had exactly ONE need — a single negotiation each, so one walk-away or one
    stall put them out of the market permanently. That is not a market
    behaviour, it is a sampling artifact: an agent that gets one attempt is
    being asked to succeed first time against a counterparty it has never
    met, which is precisely the situation the trial-size rule exists because
    agents do NOT succeed at.

    It also starved the privacy floor. Contributors are counted DISTINCT, so
    twenty merchants trading twice is worth less to the house agent than
    thirty trading once, and the floor is the gate on the whole intelligence
    economy.

    The repeat is the same need in a later round rather than a new invented
    one: a merchant that failed to buy something still needs it, and buying
    it later from someone else is the most ordinary thing in a market.
    """
    out = []
    for merchant in merchants:
        if len(merchant.needs) >= 2:
            out.append(merchant)
            continue
        first = merchant.needs[0]
        later = 4 if first.round_no < 4 else 3
        out.append(replace(merchant, needs=(
            first,
            Need(round_no=later, text=first.text,
                 qty=first.qty, limit_price=first.limit_price),
        )))
    return tuple(out)


def _a_fifth_round(merchants: tuple[Merchant, ...]) -> tuple[Merchant, ...]:
    """One more round of the same needs, for a market that keeps running.

    Not padding. Razorpay caps test-mode payment links at 30 per account, so
    a run can exhaust the quota partway and leave later merchants holding
    settlements nobody can pay. Those merchants HAVE traded — negotiated,
    passed the gate, settled — but their money has no route to move, and the
    privacy floor counts merchants whose payments actually completed.

    Re-trading them is legitimate rather than convenient: a merchant whose
    purchase could not be paid for still needs the goods, and buying again is
    what it would really do. A fifth round is also just a longer market, which
    is the ordinary thing for a market to be.
    """
    out = []
    for m in merchants:
        # The merchant's LARGEST standing need, not its first. Copying the
        # first one made a cluster merchant repeat its January quantity, so
        # total demand for the planted input fell from 2,600 back to 1,380 in
        # round 5 — demand that grew for four rounds does not snap back, and
        # the trend the house agent is meant to find would have reversed in
        # its final reading.
        biggest = max(m.needs, key=lambda n: n.qty)
        out.append(replace(m, needs=m.needs + (
            Need(round_no=5, text=biggest.text,
                 qty=biggest.qty, limit_price=biggest.limit_price),
        )))
    return tuple(out)


MERCHANTS: tuple[Merchant, ...] = _a_fifth_round(
    _give_everyone_a_second_chance(
        _SUPPLIERS + _cluster() + _PACKAGING + _TEXTILES + _ELECTRONICS + _DRY_GOODS
    )
)
