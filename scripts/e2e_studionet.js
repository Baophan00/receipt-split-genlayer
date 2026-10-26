#!/usr/bin/env node
/**
 * End to end run of ReceiptSplit on GenLayer Studio Network.
 *
 *   node scripts/e2e_studionet.js <contractAddress>
 *
 * One real receipt, three real accounts, real consensus rounds.
 *
 *   open        the owner submits the receipt text; the validators read the lines
 *   preview     the split is computed from the lines, no model involved
 *   stranger    a guest tries to settle someone else's bill: refused
 *   settle      the owner freezes the shares
 *   amend       the guest tries to edit the settled bill: refused, nothing moves
 *   stats       bills, states, settled cents
 *
 * Every amount printed here came back from a validator set, not from this script.
 */
const fs = require("fs");
const path = require("path");
const { createClient, createAccount, generatePrivateKey } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");

const ADDRESS = process.argv[2];
if (!ADDRESS) {
  console.error("usage: node scripts/e2e_studionet.js <contractAddress>");
  process.exit(1);
}

const KEYFILE = path.join(__dirname, ".e2e-keys.json");
function keys() {
  if (fs.existsSync(KEYFILE)) return JSON.parse(fs.readFileSync(KEYFILE, "utf8"));
  const k = { owner: generatePrivateKey(), guest: generatePrivateKey(), stranger: generatePrivateKey() };
  fs.writeFileSync(KEYFILE, JSON.stringify(k, null, 2), { mode: 0o600 });
  return k;
}
const k = keys();
const owner = createAccount(k.owner || k.buyer);
const guest = createAccount(k.guest || k.counterparty);
const stranger = createAccount(k.stranger || generatePrivateKey());
const ownerClient = createClient({ chain: studionet, account: owner });
const guestClient = createClient({ chain: studionet, account: guest });
const reader = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withRetry(fn, label) {
  let last;
  for (let i = 0; i < 8; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      const msg = String(e && e.message);
      if (!/DOCTYPE|fetch failed|ECONNRESET|timeout|temporarily unavailable|not available/i.test(msg)) throw e;
      console.log(`  ${label}: rpc hiccup, retry ${i + 1}`);
      await sleep(4000);
    }
  }
  throw last;
}

function refused(receipt) {
  const leader = (receipt.consensus_data && receipt.consensus_data.leader_receipt) || [];
  return String(leader[0] && leader[0].execution_result).toUpperCase() === "ERROR";
}

function money(cents) {
  const n = Math.abs(Number(cents));
  return `${n < 0 ? "-" : ""}${Math.floor(n / 100)}.${String(n % 100).padStart(2, "0")}`;
}

async function send(client, who, functionName, args) {
  console.log(`\n[${who}] ${functionName}(${args.map((a) => JSON.stringify(a).slice(0, 60)).join(", ")})`);
  const hash = await client.writeContract({ address: ADDRESS, functionName, args, value: 0n });
  console.log(`  tx ${hash}`);
  const receipt = await withRetry(
    () => client.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 200, interval: 3000 }),
    "receipt"
  );
  const verdict = refused(receipt) ? "refused by the contract" : "executed";
  console.log(`  ${receipt.status_name || receipt.status}  (${verdict})`);
  return { hash, refused: refused(receipt) };
}

async function write(client, who, functionName, args) {
  const { hash, refused: reverted } = await send(client, who, functionName, args);
  if (reverted) throw new Error(`${functionName} failed inside the contract`);
  return hash;
}

async function expectRefused(client, who, functionName, args, why) {
  const { hash, refused: reverted } = await send(client, who, functionName, args);
  if (!reverted) throw new Error(`${who} was allowed to ${functionName}: ${why}`);
  console.log(`  refused as expected: ${why} (tx ${hash})`);
  return hash;
}

const read = (functionName, args) =>
  withRetry(() => reader.readContract({ address: ADDRESS, functionName, args }), "read");

// A receipt a person would actually photograph: printed names, a table, a total.
const RECEIPT = [
  "THE CORNER DELI",
  "Table 6      server: Marta",
  "2x Espresso .............. 7.00",
  "1x Cheesecake ............ 6.50",
  "1x Club Sandwich ........ 12.75",
  "Subtotal ................ 26.25",
  "Total ................... 26.25",
  "Card .................... 26.25",
].join("\n");

const PEOPLE = "ana,beto,carla";

(async () => {
  const billId = process.argv[3] || `lunch-${Date.now().toString(36)}`;
  console.log("contract", ADDRESS);
  console.log("bill    ", billId);
  console.log("owner   ", owner.address);
  console.log("guest   ", guest.address);
  console.log("stranger", stranger.address);

  console.log("\n[validators] read the receipt");
  const t0 = Date.now();
  await write(ownerClient, "owner", "open_bill", [billId, "Delhi team lunch", RECEIPT, PEOPLE]);
  console.log(`  read took ${Math.round((Date.now() - t0) / 1000)}s`);

  let bill = await read("get_bill", [billId]);
  console.log(`  state: ${bill.state} | rev: ${bill.rev} | parsed_rev: ${bill.parsed_rev}`);
  console.log("  items the validators reported:");
  for (const item of bill.items) {
    console.log(`    ${item.name.padEnd(16)} ${money(item.amount).padStart(7)}`);
  }
  console.log(`  items total ${money(bill.items_cents)} against a stated total of ${money(bill.total_cents)}`);
  if (Number(bill.items_cents) !== Number(bill.total_cents)) {
    throw new Error("the parsed lines do not add up to the stated total");
  }

  console.log("\n[contract] the split, computed from those lines (no model)");
  const preview = await read("preview_split", [billId]);
  for (const person of preview.people) {
    console.log(`  ${person.padEnd(8)} ${money(preview.per_person[person]).padStart(7)}`);
  }
  for (const note of preview.notes) console.log(`  note: ${note}`);
  const summed = preview.people.reduce((acc, p) => acc + Number(preview.per_person[p]), 0);
  console.log(`  ${preview.people.length} people, ${money(summed)} in total, bill is ${money(preview.total_cents)}`);
  if (summed !== Number(preview.total_cents)) throw new Error("the split does not sum to the bill");

  console.log("\n[guard] a guest tries to settle someone else's bill");
  await expectRefused(guestClient, "guest", "settle", [billId], "only the owner settles the bill");

  console.log("\n[owner] settle");
  await write(ownerClient, "owner", "settle", [billId]);
  bill = await read("get_bill", [billId]);
  console.log(`  state: ${bill.state} | shares: ${JSON.stringify(bill.shares)}`);
  if (bill.state !== "settled") throw new Error(`expected settled, got ${bill.state}`);

  console.log("\n[guard] the guest tries to edit the frozen bill");
  const edited = JSON.stringify([{ name: "Espresso", amount: 1 }]);
  await expectRefused(guestClient, "guest", "amend_items", [billId, edited], "only the owner edits the bill");
  bill = await read("get_bill", [billId]);
  console.log(`  after the attempt: state ${bill.state} | rev ${bill.rev} | items_cents ${bill.items_cents}`);
  if (bill.rev !== "1") throw new Error("a refused amend moved the revision");
  if (Number(bill.items_cents) !== Number(bill.total_cents)) {
    throw new Error("a refused amend changed the items");
  }

  console.log("\nstats");
  console.log(JSON.stringify(await read("stats", []), null, 2));
  console.log(
    "\nRESULT: lines read by consensus, split computed from them, guest refused on settle and on amend, bill frozen with its shares"
  );
})().catch((e) => {
  console.error("\nFAILED:", e.message);
  if (e.cause) console.error(String(e.cause).slice(0, 900));
  process.exit(1);
});
