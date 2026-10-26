# ReceiptSplit

One receipt in, one split out, and nobody in the group chat gets to type the numbers.

Four people photograph a receipt and the argument is never about who was there. It is about the amounts: one
reads a total off the photo, another adds the lines up in their head, a third "remembers" a service charge, and
the split that actually gets paid is the one typed into the chat by whoever was loudest. ReceiptSplit replaces
that with a public bill: a validator set reads the receipt once and reports what it charges for, the contract
divides that by code, and the owner freezes the result.

## The interesting part: consensus on the amounts, arithmetic on the split

The model is asked one thing, and it is not the interesting one:

```python
gl.eq_principle.prompt_comparative(
    ask,
    principle="Both answers must name the same number of items and, for the item in each "
              "position, the same amount in cents. The wording of a name may differ (a receipt "
              "prints abbreviations, one reader may expand them), but any amount that differs by "
              "a single cent, or a different number of items, is not a match.",
)
```

Every validator reads the same receipt text independently and reports `{"items": [{"name", "amount"}],
"total_cents"}` with amounts in whole cents. The equivalence principle compares amounts, not prose: a receipt
prints `CLUB SNDWCH` and a reader may write `Club Sandwich`, which is fine, but 1275 and 1257 are two different
bills. That is the whole reason a validator set is here — two readers can disagree about an amount on a photo of
a receipt, and the party who submitted the photo is the party who benefits from the numbers.

Nothing else is delegated. The model never divides, never rounds, never converts a currency and never decides
who owes what. The split is a pure function of the item list, in the contract, identical on every node:

```python
base, left = divmod(amount, n)                 # every line, split n ways
shares[people[i]] = base + (1 if i < left else 0)
```

Leftover cents go to the people first in the bill's list, one cent each, so the `amount % n` cents that cannot
be divided are never rounded away. A 10.00 pizza between three people is 334 / 333 / 333 — not 333.33 three
times, which silently loses a cent, and not "about 3.33 each", which loses the argument. Per-person totals
always sum to the bill, per-line shares always sum to the line, and `preview_split` returns the per-line shares
and a note for every line that left a remainder, so a split can be audited by hand.

## Why the revision bookkeeping matters

A verdict from a validator set is only true of the exact text it was computed on, and that is what `rev` and
`parsed_rev` are for:

- every change to the item lines moves `rev`; a read of the receipt records the revision it covered in
  `parsed_rev`;
- `open_bill` parses, so a new bill starts at `rev = parsed_rev = 1`;
- `amend_items` moves `rev` and leaves `parsed_rev` behind. The bill now visibly holds numbers a party typed,
  `get_bill` reports `needs_reparse: true`, and `settle` refuses it;
- `reparse` reads the receipt again, and never overwrites a hand edit. If the owner has edited the lines, the
  fresh read either lands on exactly the same cents — the edit is confirmed, `parsed_rev` catches up and nothing
  is touched — or it refuses with `the items were edited by hand` and the bill stays as it was;
- `settle` requires `parsed_rev == rev`. The numbers a bill settles on are numbers a validator set produced,
  never numbers an interested party typed in, and a hand correction can only become settleable by an
  independent read that agrees with it to the cent;
- a settled bill is frozen: no amend, no reparse, no second settle, and its id cannot be reused.

The alternative — settle whatever is in storage, and let the owner amend after the read — makes the whole
consensus round decorative, because the numbers that get paid would be the ones typed in afterwards.

## Strict, because a bad answer is not a cheap lunch

The parsed answer is validated before anything is stored, and the same validator handles the owner's hand
edits, so neither path can store what the other would reject:

- a dict with an `items` list (a bare list is accepted from the owner), 1 to 40 entries;
- each entry `{name, amount}` with a non-empty name up to 80 characters and an amount that is an integer
  number of cents, greater than zero. `true` is not one cent, `"700"` is not a number, `7.5` is not a whole
  number of cents, and there is no "about 700";
- an optional `total_cents`; when the receipt states a total, `abs(sum(items) - total) <= 2` is enforced at
  open, amend and reparse time. Two cents of slack is rounding on a tax line; 1.25 missing is a lost item, and
  the bill is refused with `the items do not add up to the stated total`;
- anything else is a `gl.vm.UserError` raised before the bill is stored. A malformed answer never reads as
  "no items".

The prompt also tells the model that the receipt is untrusted data and that a receipt instructing it what to
answer is to be disregarded. A test pins that wording: the mock only fires if the prompt carries it.

## Flow

1. `open_bill(bill_id, title, receipt_text, people)` — first write wins. `people` is comma separated, at most
   eight, no duplicates, and its order is the order leftover cents are handed out in. The validators read the
   receipt, the total is checked, and the bill opens at revision 1.
2. `preview_split(bill_id)` — the split, computed now, per person and per line, with a note for every line that
   left a remainder. Nothing is written.
3. `settle(bill_id)` — owner only, and only when `parsed_rev == rev`. Stores the shares and freezes the bill.
4. `amend_items(bill_id, items_json)` — owner only, before settlement. Corrects the lines; the bill stops
   matching any read of it.
5. `reparse(bill_id)` — owner only, before settlement. Reads the receipt again, confirms a hand edit that the
   validators agree with, and refuses rather than overwrite one they do not.
6. `get_bill`, `bills_of(address)`, `bill_count`, `stats` — read side.

Limits are deliberate: 40 charge lines, 8 people, 8000 characters of receipt text. Enough for a real table,
small enough that a validator reading it cannot drift.

## Contract surface

| kind | name |
| --- | --- |
| write | `open_bill`, `amend_items`, `reparse`, `settle` |
| view | `get_bill`, `preview_split`, `bills_of`, `bill_count`, `stats` |

## Tests

```bash
python -m pytest tests/direct -q     # 47 passed
```

The model is mocked; the contract is not. Every test drives the same code the validators run: the item parser,
the total check, the revision bookkeeping and the largest-remainder split.

- `TestOpen` — the parsed bill, ownership, an id written once (including after settlement), the people rules,
  title and receipt requirements, and the untrusted-data wording in the prompt;
- `TestStrictParse` — a missing item list, a list that is not a list, an empty list, negative, zero, fractional,
  textual and boolean amounts, an item with no name or no amount, 41 items, an answer that is not JSON, a total
  that does not add up, the two-cent tolerance, and a receipt that states no total;
- `TestSplit` — two people, three people, a single person, leftover cents to the first people in the list, a
  single odd cent, 999 cents over eight people, the same split twice, and preview writing nothing;
- `TestSettle` — shares recorded and frozen, owner only, once only, unknown bill;
- `TestAmend` — owner only, `rev` moves and `parsed_rev` does not, settle refused afterwards, the same strict
  validation as the parser, the receipt's stated total still enforced, and a settled bill frozen;
- `TestReparse` — refreshing a parsed bill, a hand edit a fresh read confirms (which then settles), a hand edit a
  fresh read disagrees with (refused, and the edit survives), owner only;
- `TestViews` — `bills_of` per address, `bill_count`, `stats`, and an empty contract.

## Deploy and run

```bash
npm install
node scripts/deploy_studionet.js                        # prints the deploy hash and the address
node scripts/e2e_studionet.js <contractAddress>         # real rounds against that address
```

`deploy_studionet.js` prints `deployer`, `deploy tx`, `contract`, then reads `stats()` back, because a deploy
whose `__init__` reverted still produces a receipt. The first run of either script creates
`scripts/.e2e-keys.json` with fresh local keys (gitignored, mode 0600); nothing is committed and no key leaves
the machine.

`e2e_studionet.js` submits a real deli receipt to a real validator set, prints the lines it reported, prints
the split, and then exercises the guards: a guest is refused on `settle`, and after the bill is settled the
guest is refused on `amend_items` while the revision and the items stay exactly as they were. Refusals are
detected from the receipt itself (`consensus_data.leader_receipt[0].execution_result === "ERROR"`), not from a
thrown JavaScript error.

## Layout

```
contracts/receipt_split.py     the contract
tests/direct/                  direct-mode suite, 47 tests
scripts/deploy_studionet.js    deploy with genlayer-js, no CLI
scripts/e2e_studionet.js       the run above, against a deployed address
```
