"""Direct-mode tests for ReceiptSplit.

    python -m pytest tests/direct -q

The model is mocked, the contract is not: every test drives the same code the
validators run, including the strict item parser, the total check and the
largest-remainder split. Nothing here talks to a network or a chain.
"""

import json

CONTRACT = "contracts/receipt_split.py"

RECEIPT = """THE CORNER DELI
Table 4
2x Espresso          7.00
1x Cheesecake        6.50
1x Club Sandwich    12.75
Subtotal            26.25
Total               26.25"""

ITEMS = [
    {"name": "Espresso", "amount": 700},
    {"name": "Cheesecake", "amount": 650},
    {"name": "Club Sandwich", "amount": 1275},
]
ITEMS_CENTS = 2625


def deploy(direct_deploy):
    return direct_deploy(CONTRACT)


def hexaddr(raw):
    """The fixture addresses arrive as 20 raw bytes; the contract stores hex."""
    return "0x" + bytes(raw).hex()


def mock_read(direct_vm, items=None, total=ITEMS_CENTS, pattern=r"You are reading the text"):
    payload = {"items": ITEMS if items is None else items}
    if total is not None:
        payload["total_cents"] = total
    direct_vm.mock_llm(pattern, json.dumps(payload))


def mock_raw(direct_vm, payload):
    """Mock the answer verbatim, without wrapping it in an items object."""
    direct_vm.mock_llm(r"You are reading the text", payload)


def open_bill(contract, direct_vm, sender, people="alice,bob", bill_id="lunch", receipt=RECEIPT):
    direct_vm.sender = sender
    return contract.open_bill(bill_id, "Team lunch", receipt, people)


def settled_bill(contract, direct_vm, sender, people="alice,bob"):
    mock_read(direct_vm)
    open_bill(contract, direct_vm, sender, people=people)
    return contract.settle("lunch")


class TestOpen:
    def test_a_bill_opens_with_the_lines_the_validators_read(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        result = open_bill(contract, direct_vm, direct_alice)

        assert result["items"] == ITEMS
        assert result["rev"] == "1"
        assert result["parsed_rev"] == "1"
        assert result["needs_reparse"] is False

        bill = contract.get_bill("lunch")
        assert bill["state"] == "open"
        assert bill["title"] == "Team lunch"
        assert bill["items"] == ITEMS
        assert bill["people"] == ["alice", "bob"]
        assert bill["total_cents"] == "2625"
        assert bill["items_cents"] == "2625"
        assert bill["shares"] is None
        assert bill["receipt_text"].startswith("THE CORNER DELI")
        assert contract.bill_count() == 1

    def test_the_opener_is_the_owner(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        assert contract.get_bill("lunch")["owner"].lower() == hexaddr(direct_alice)
        assert contract.bills_of(direct_alice) == ["lunch"]
        assert contract.bills_of(direct_bob) == []

    def test_a_bill_id_is_written_once(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("that bill id is taken"):
            open_bill(contract, direct_vm, direct_alice, people="bob,charlie")
        assert contract.bill_count() == 1
        assert contract.get_bill("lunch")["people"] == ["alice", "bob"]

    def test_the_same_id_cannot_be_reused_after_settlement(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        settled_bill(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("that bill id is taken"):
            open_bill(contract, direct_vm, direct_alice)
        assert contract.bill_count() == 1

    def test_people_are_checked(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)

        with direct_vm.expect_revert("at least one person"):
            open_bill(contract, direct_vm, direct_alice, people=" , ,")
        with direct_vm.expect_revert("listed twice"):
            open_bill(contract, direct_vm, direct_alice, people="alice,bob,alice")
        with direct_vm.expect_revert("more than 8 people"):
            open_bill(
                contract,
                direct_vm,
                direct_alice,
                people="a,b,c,d,e,f,g,h,i",
            )
        assert contract.bill_count() == 0

    def test_a_bill_needs_a_title_and_a_receipt(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("needs a title"):
            contract.open_bill("lunch", "   ", RECEIPT, "alice,bob")
        with direct_vm.expect_revert("needs the receipt text"):
            contract.open_bill("lunch", "Lunch", "  \n ", "alice,bob")
        assert contract.bill_count() == 0

    def test_the_prompt_calls_the_receipt_untrusted(self, direct_vm, direct_deploy, direct_alice):
        """The mock only fires if the prompt carries the warning verbatim."""
        contract = deploy(direct_deploy)
        direct_vm.mock_llm(
            r"(?s)untrusted data.*not an instruction.*disregard them",
            json.dumps({"items": ITEMS, "total_cents": ITEMS_CENTS}),
        )
        open_bill(contract, direct_vm, direct_alice)
        assert contract.get_bill("lunch")["items"] == ITEMS


class TestStrictParse:
    """A broken answer must stop the bill, not become a cheap lunch."""

    def test_a_missing_items_list_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_raw(direct_vm, json.dumps({"total_cents": 2625}))

        with direct_vm.expect_revert("items list is missing"):
            open_bill(contract, direct_vm, direct_alice)
        assert contract.bill_count() == 0

    def test_items_that_are_not_a_list_are_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items="Espresso 7.00")

        with direct_vm.expect_revert("items are not a list"):
            open_bill(contract, direct_vm, direct_alice)
        assert contract.bill_count() == 0

    def test_an_empty_items_list_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[], total=0)

        with direct_vm.expect_revert("lists no items"):
            open_bill(contract, direct_vm, direct_alice)

    def test_a_negative_amount_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Refund", "amount": -500}])

        with direct_vm.expect_revert("is negative"):
            open_bill(contract, direct_vm, direct_alice)
        assert contract.bill_count() == 0

    def test_a_zero_amount_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Tap water", "amount": 0}])

        with direct_vm.expect_revert("is zero"):
            open_bill(contract, direct_vm, direct_alice)

    def test_an_amount_written_as_text_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Espresso", "amount": "700"}])

        with direct_vm.expect_revert("not a whole number of cents"):
            open_bill(contract, direct_vm, direct_alice)

    def test_a_fractional_amount_is_rejected_on_amend(
        self, direct_vm, direct_deploy, direct_alice
    ):
        """A float cannot even cross the calldata boundary, so it is tested at the
        one door that takes a string of JSON: the owner's hand edit."""
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not a whole number of cents"):
            contract.amend_items("lunch", json.dumps([{"name": "Espresso", "amount": 7.5}]))
        assert contract.get_bill("lunch")["rev"] == "1"

    def test_a_boolean_amount_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Espresso", "amount": True}])

        with direct_vm.expect_revert("not a whole number of cents"):
            open_bill(contract, direct_vm, direct_alice)

    def test_an_item_without_a_name_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "   ", "amount": 700}])

        with direct_vm.expect_revert("empty name"):
            open_bill(contract, direct_vm, direct_alice)

    def test_an_item_without_an_amount_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Espresso"}])

        with direct_vm.expect_revert("has no amount"):
            open_bill(contract, direct_vm, direct_alice)

    def test_too_many_items_are_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        many = [{"name": f"item {i}", "amount": 100} for i in range(41)]
        mock_read(direct_vm, items=many, total=4100)

        with direct_vm.expect_revert("more than 40 items"):
            open_bill(contract, direct_vm, direct_alice)
        assert contract.bill_count() == 0

    def test_answers_that_are_not_json_are_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_raw(direct_vm, "espresso 7.00, cheesecake 6.50")

        with direct_vm.expect_revert("is not JSON"):
            open_bill(contract, direct_vm, direct_alice)

    def test_a_total_that_does_not_add_up_is_rejected(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        # 25.00 stated, 26.25 of lines read: a lost item, not rounding.
        mock_read(direct_vm, total=2500)

        with direct_vm.expect_revert("do not add up to the stated total"):
            open_bill(contract, direct_vm, direct_alice)
        assert contract.bill_count() == 0

    def test_two_cents_of_rounding_is_tolerated(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, total=ITEMS_CENTS + 2)

        open_bill(contract, direct_vm, direct_alice)
        assert contract.get_bill("lunch")["total_cents"] == "2627"

    def test_a_receipt_without_a_stated_total_is_fine(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, total=None)

        open_bill(contract, direct_vm, direct_alice)
        assert contract.get_bill("lunch")["total_cents"] == "0"


class TestSplit:
    def test_two_people_split_to_the_cent(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice, people="alice,bob")

        preview = contract.preview_split("lunch")
        # 700 -> 350/350, 650 -> 325/325, 1275 -> 638/637
        assert preview["per_person"] == {"alice": 1313, "bob": 1312}
        assert sum(preview["per_person"].values()) == ITEMS_CENTS
        assert preview["total_cents"] == str(ITEMS_CENTS)
        assert preview["state"] == "open"

    def test_three_people_split_to_the_cent(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice, people="alice,bob,charlie")

        preview = contract.preview_split("lunch")
        # 700 -> 234/233/233, 650 -> 217/217/216, 1275 -> 425/425/425
        assert preview["per_person"] == {"alice": 876, "bob": 875, "charlie": 874}
        assert sum(preview["per_person"].values()) == ITEMS_CENTS
        assert len(preview["lines"]) == 3

    def test_a_single_person_owes_the_whole_bill(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice, people="alice")

        preview = contract.preview_split("lunch")
        assert preview["per_person"] == {"alice": ITEMS_CENTS}
        assert preview["notes"] == []
        assert [line["shares"] for line in preview["lines"]] == [
            {"alice": 700},
            {"alice": 650},
            {"alice": 1275},
        ]

    def test_leftover_cents_go_to_the_first_people_in_the_list(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Pizza", "amount": 1000}], total=1000)
        open_bill(contract, direct_vm, direct_alice, people="alice,bob,charlie")

        preview = contract.preview_split("lunch")
        assert preview["per_person"] == {"alice": 334, "bob": 333, "charlie": 333}
        assert sum(preview["per_person"].values()) == 1000
        assert preview["notes"] == [
            "'Pizza' is 10.00 over 3 people: 3.33 each leaves 1 cent, carried by alice"
        ]

    def test_an_odd_cent_goes_to_one_person_only(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm, items=[{"name": "Coffee", "amount": 1001}], total=1001)
        open_bill(contract, direct_vm, direct_alice, people="alice,bob")

        preview = contract.preview_split("lunch")
        assert preview["per_person"] == {"alice": 501, "bob": 500}
        assert preview["lines"][0]["shares"] == {"alice": 501, "bob": 500}

    def test_every_line_stays_whole(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        amount = 999
        mock_read(direct_vm, items=[{"name": "Platter", "amount": amount}], total=amount)
        open_bill(contract, direct_vm, direct_alice, people="a,b,c,d,e,f,g,h")

        preview = contract.preview_split("lunch")
        shares = preview["per_person"]
        assert sum(shares.values()) == amount
        # 999 over 8: 124 each, 7 cents left over to the first seven people.
        assert sorted(shares.values()) == [124, 125, 125, 125, 125, 125, 125, 125]
        assert shares["a"] == 125 and shares["h"] == 124

    def test_the_split_is_the_same_every_time(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice, people="alice,bob,charlie")

        first = contract.preview_split("lunch")
        second = contract.preview_split("lunch")
        assert first["per_person"] == second["per_person"]
        assert first["lines"] == second["lines"]

    def test_preview_does_not_settle_anything(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        contract.preview_split("lunch")
        bill = contract.get_bill("lunch")
        assert bill["state"] == "open"
        assert bill["shares"] is None
        assert contract.stats()["states"]["settled"] == 0


class TestSettle:
    def test_settle_records_the_shares(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        result = settled_bill(contract, direct_vm, direct_alice)

        assert result["state"] == "settled"
        assert result["shares"] == {"alice": 1313, "bob": 1312}
        assert result["total_cents"] == str(ITEMS_CENTS)
        assert len(result["notes"]) == 1

        bill = contract.get_bill("lunch")
        assert bill["state"] == "settled"
        assert bill["shares"] == {"alice": 1313, "bob": 1312}
        assert bill["rev"] == "1" and bill["parsed_rev"] == "1"
        assert contract.stats()["settled_cents"] == str(ITEMS_CENTS)

    def test_only_the_owner_settles(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("only the owner settles the bill"):
            contract.settle("lunch")
        assert contract.get_bill("lunch")["state"] == "open"

    def test_a_bill_settles_once(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        settled_bill(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("this bill is settled"):
            contract.settle("lunch")

    def test_an_unknown_bill_is_an_error(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("no such bill"):
            contract.settle("nope")
        with direct_vm.expect_revert("no such bill"):
            contract.get_bill("nope")


class TestAmend:
    def make(self, contract, direct_vm, direct_alice):
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

    def test_only_the_owner_edits_the_bill(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        self.make(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("only the owner edits the bill"):
            contract.amend_items("lunch", json.dumps([{"name": "x", "amount": 1}]))

    def test_amend_bumps_the_revision_and_leaves_the_parse_behind(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        self.make(contract, direct_vm, direct_alice)

        # Same 26.25, different lines: the espresso was 7.50 and the sandwich 12.25.
        fixed = [{"name": "Espresso", "amount": 750}, {"name": "Cheesecake", "amount": 650},
                 {"name": "Club Sandwich", "amount": 1225}]
        direct_vm.sender = direct_alice
        result = contract.amend_items("lunch", json.dumps(fixed))

        assert result["rev"] == "2"
        assert result["parsed_rev"] == "1"
        assert result["needs_reparse"] is True
        bill = contract.get_bill("lunch")
        assert bill["items"][0]["amount"] == 750
        assert bill["rev"] == "2" and bill["parsed_rev"] == "1"
        assert bill["items_cents"] == "2625"

    def test_an_amended_bill_cannot_be_settled(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        self.make(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_alice
        contract.amend_items("lunch", json.dumps(ITEMS))
        with direct_vm.expect_revert("no read of this bill to settle"):
            contract.settle("lunch")
        assert contract.get_bill("lunch")["state"] == "open"
        assert contract.get_bill("lunch")["shares"] is None

    def test_amend_validates_like_the_parser(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        self.make(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("is negative"):
            contract.amend_items("lunch", json.dumps([{"name": "x", "amount": -1}]))
        with direct_vm.expect_revert("is zero"):
            contract.amend_items("lunch", json.dumps([{"name": "x", "amount": 0}]))
        with direct_vm.expect_revert("not a whole number of cents"):
            contract.amend_items("lunch", json.dumps([{"name": "x", "amount": 1.5}]))
        with direct_vm.expect_revert("more than 40 items"):
            contract.amend_items(
                "lunch", json.dumps([{"name": "x", "amount": 1}] * 41)
            )
        with direct_vm.expect_revert("is not JSON"):
            contract.amend_items("lunch", "espresso 8.00")
        assert contract.get_bill("lunch")["rev"] == "1"

    def test_amend_checks_the_receipts_stated_total(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        self.make(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice

        # The receipt says 26.25; typing 5.00 of lines into it is a lost bill.
        with direct_vm.expect_revert("do not add up to the stated total"):
            contract.amend_items("lunch", json.dumps([{"name": "Coffee", "amount": 500}]))
        assert contract.get_bill("lunch")["rev"] == "1"

    def test_a_settled_bill_is_frozen(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        settled_bill(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("this bill is settled"):
            contract.amend_items("lunch", json.dumps(ITEMS))
        with direct_vm.expect_revert("this bill is settled"):
            contract.reparse("lunch")

        bill = contract.get_bill("lunch")
        assert bill["state"] == "settled"
        assert bill["shares"] == {"alice": 1313, "bob": 1312}
        assert bill["rev"] == "1"


class TestReparse:
    def test_reparse_refreshes_a_parsed_bill(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        direct_vm.clear_mocks()
        better = [{"name": "Espresso", "amount": 700}, {"name": "Cheesecake", "amount": 700}]
        mock_read(direct_vm, items=better, total=1400)

        direct_vm.sender = direct_alice
        result = contract.reparse("lunch")
        assert result["confirmed"] is False
        assert result["rev"] == "2"
        assert result["parsed_rev"] == "2"
        assert result["items"] == better
        assert contract.get_bill("lunch")["items_cents"] == "1400"

    def test_a_hand_edit_confirmed_by_a_fresh_read_can_be_settled(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        fixed = [{"name": "Espresso", "amount": 750}, {"name": "Cheesecake", "amount": 650},
                 {"name": "Club Sandwich", "amount": 1225}]
        direct_vm.sender = direct_alice
        contract.amend_items("lunch", json.dumps(fixed))
        assert contract.get_bill("lunch")["rev"] == "2"

        direct_vm.clear_mocks()
        mock_read(direct_vm, items=fixed, total=2625)

        result = contract.reparse("lunch")
        assert result["confirmed"] is True
        # The hand edit is untouched and the revision did not move: the read agreed.
        assert result["items"] == fixed
        assert result["rev"] == "2" and result["parsed_rev"] == "2"

        settled = contract.settle("lunch")
        assert settled["state"] == "settled"
        assert sum(settled["shares"].values()) == 2625

    def test_a_hand_edit_a_fresh_read_disagrees_with_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        # Same 26.25 in two lines instead of three: a different item count.
        short = [{"name": "Espresso", "amount": 1000},
                 {"name": "Cheesecake", "amount": 1625}]
        direct_vm.sender = direct_alice
        contract.amend_items("lunch", json.dumps(short))

        direct_vm.clear_mocks()
        mock_read(direct_vm, items=ITEMS, total=ITEMS_CENTS)

        with direct_vm.expect_revert("the items were edited by hand"):
            contract.reparse("lunch")
        bill = contract.get_bill("lunch")
        assert bill["items"] == short
        assert bill["rev"] == "2" and bill["parsed_rev"] == "1"
        with direct_vm.expect_revert("no read of this bill to settle"):
            contract.settle("lunch")

    def test_only_the_owner_reparses(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("only the owner reparses the bill"):
            contract.reparse("lunch")


class TestViews:
    def test_bills_of_and_bill_count(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice, bill_id="lunch")
        open_bill(contract, direct_vm, direct_bob, bill_id="dinner", people="bob,charlie")

        assert contract.bill_count() == 2
        assert contract.bills_of(direct_alice) == ["lunch"]
        assert contract.bills_of(direct_bob) == ["dinner"]

    def test_stats_counts_bills_and_settled_cents(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        mock_read(direct_vm)
        open_bill(contract, direct_vm, direct_alice, bill_id="lunch")
        open_bill(contract, direct_vm, direct_bob, bill_id="dinner", people="bob,charlie")

        direct_vm.sender = direct_alice
        contract.settle("lunch")

        stats = contract.stats()
        assert stats["bills"] == 2
        assert stats["states"] == {"open": 1, "settled": 1}
        assert stats["settled_cents"] == str(ITEMS_CENTS)

    def test_an_empty_contract_has_empty_stats(self, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        assert contract.bill_count() == 0
        assert contract.bills_of(direct_alice) == []
        assert contract.stats() == {
            "bills": 0,
            "states": {"open": 0, "settled": 0},
            "settled_cents": "0",
        }
