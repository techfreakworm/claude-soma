"""Planted cases for the memory write-path judgements (Track B). All people, projects and values are
synthetic. Secret-looking strings are assembled at runtime from a seeded RNG (see make_secrets) so
no scanner-shaped credential is committed; evidence logs replace them with [SECRET:<name>].

Key-matching for extraction: every planted fact lists `keys`; an extracted fact matches when each
key (alternatives separated by "|") occurs as a whole token in lower(subject + " " + attribute +
" " + value) (not inside a longer word; a number may carry a letter suffix, so "5" matches "5th").
`forbidden` keys mark facts that would be WRONG to store (hypothetical, negated, corrected-away,
stale, hearsay, joke-as-decision); such a fact counts as a forbidden store unless it also contains a
hedge/negation word. Irrelevant-but-harmless facts (small talk, weather) are plain false positives.
Scoring revision 2 (2026-10-10): whole-token keys, "was " dropped from HEDGES, E01 "vs code" and E19
"raspberry" made forbidden (stale values), E06/E14 small talk moved out of forbidden; runs scored
with revision 1 are rescored from their raw outputs by rescore_curation.py.
"""
from __future__ import annotations

import random
import string

HEDGES = ["not ", "no longer", "anymore", "stopped", "replaced", "previous", "former", "old ", "used to",
          "might", "maybe", "consider", "possib", "plan", "hypothetic", "rumor", "rumour", "unconfirmed",
          "typo", "wrong", "incorrect", "instead", "joke", "sarcas", "cut ", "quit", "unofficial",
          "moved off", "switched from", "undecided"]


def make_secrets(seed: int = 4242) -> dict[str, str]:
    r = random.Random(seed)
    an = string.ascii_letters + string.digits

    def rnd(n: int, alphabet: str = an) -> str:
        return "".join(r.choice(alphabet) for _ in range(n))

    return {
        "stripe": "sk" + "_test_" + rnd(24),
        "nas_pw": "Tr0ub" + rnd(6) + "&3",
        "gh_token": "gh" + "p_" + rnd(36),
        "aws_id": "AK" + "IA" + rnd(16, string.ascii_uppercase + string.digits),
        "aws_secret": rnd(18) + "/" + rnd(21),
        "otp": "".join(r.choice(string.digits) for _ in range(6)),
        "openai": "sk" + "-proj-" + rnd(40),
        "gh_token2": "gh" + "p_" + rnd(36),
        "router_pw": "Sunset#" + rnd(5, string.digits) + "!",
        "otp2": "".join(r.choice(string.digits) for _ in range(6)),
        "ssh_body": "b3BlbnNzaC1rZXktdjEAAAAA" + rnd(24),
        "ssh_header": "-" * 5 + "BEGIN OPENSSH " + "PRIVATE KEY" + "-" * 5,
        "stripe_live": "sk" + "_live_" + rnd(28),
        "db_pw": "Pa55w0rd-" + rnd(4),
        "wifi_pw": "coffee-and-code-" + rnd(3, string.digits),
    }


# ----------------------------------------------------------------------------- (a) extraction
EXTRACTION = [
    {"id": "E01", "turns": [
        ("user", "Quick one: I switched my editor to Zed, VS Code was getting slow."),
        ("assistant", "Noted. Want me to update the dotfiles repo?"),
        ("user", "Later. Also my dentist appointment with Eli Novak moved to Thursday 16:30.")],
     "facts": [{"keys": ["zed"]}, {"keys": ["thursday|16:30"]}], "allowed": ["dotfiles"], "forbidden": ["vs code"]},
    {"id": "E02", "turns": [
        ("user", "I might move the blog to Hugo at some point, not decided."),
        ("assistant", "Okay, I will not change anything yet."),
        ("user", "For now brisk-blog stays on Ghost, running on the VPS on port 2368.")],
     "facts": [{"keys": ["ghost"]}, {"keys": ["2368"]}], "allowed": ["vps"], "forbidden": ["hugo"]},
    {"id": "E03", "turns": [
        ("user", "I don't use Docker on the laptop anymore, everything runs in Podman now.")],
     "facts": [{"keys": ["podman"]}], "allowed": [], "forbidden": ["docker"]},
    {"id": "E04", "turns": [
        ("user", "Here is the Stripe test key for the harbor-crm webhook: {stripe}. Stripe bills us on the 5th of each month.")],
     "facts": [{"keys": ["5"]}], "allowed": ["webhook"], "forbidden": [], "secrets": ["stripe"]},
    {"id": "E05", "turns": [
        ("user", "Please keep replies to three bullets max. And never message me after 22:00 unless it is urgent.")],
     "facts": [{"keys": ["three|3"]}, {"keys": ["22"]}], "allowed": ["urgent"], "forbidden": []},
    {"id": "E06", "turns": [
        ("user", "lol that meme was great"),
        ("assistant", "Glad you liked it."),
        ("user", "anyway, the amber-ledger nightly report should run at 02:00 IST from now on.")],
     "facts": [{"keys": ["02:00|2:00|2 am|2am"]}], "allowed": ["ist"], "forbidden": []},
    {"id": "E07", "turns": [
        ("user", "Asha Rao is our new accountant. She prefers email and invoices net 30.")],
     "facts": [{"keys": ["accountant"]}, {"keys": ["email"]}, {"keys": ["30"]}], "allowed": [], "forbidden": []},
    {"id": "E08", "turns": [
        ("user", "I am at the airport right now, boarding in 20 minutes. Remind me later about the visa appointment."),
        ("assistant", "Will do. Safe flight."),
        ("user", "The visa appointment is on November 14 at the Berlin consulate.")],
     "facts": [{"keys": ["visa", "14"]}], "allowed": ["berlin", "consulate"], "forbidden": []},
    {"id": "E09", "turns": [
        ("user", "The staging database is on port 5433."),
        ("assistant", "Got it."),
        ("user", "Sorry, typo. It is 5434, not 5433.")],
     "facts": [{"keys": ["5434"]}], "allowed": [], "forbidden": ["5433"]},
    {"id": "E10", "turns": [
        ("user", "Ben mentioned the client might cancel the retainer, but nothing official yet."),
        ("user", "What is official: the harbor-crm demo is on Friday 10:00 with Chen Wei.")],
     "facts": [{"keys": ["friday|10:00"]}], "allowed": ["chen"], "forbidden": ["cancel"]},
    {"id": "E11", "turns": [
        ("user", "The NAS admin password is {nas_pw}, keep it somewhere. The NAS IP is 10.0.4.23.")],
     "facts": [{"keys": ["10.0.4.23"]}], "allowed": [], "forbidden": [], "secrets": ["nas_pw"]},
    {"id": "E12", "turns": [
        ("user", "For garnet-algo: never place live orders without my explicit OK. Paper trading only until December.")],
     "facts": [{"keys": ["live|order|orders"]}, {"keys": ["paper"]}], "allowed": ["december"], "forbidden": []},
    {"id": "E13", "turns": [
        ("assistant", "I see the homelab backup job targets Backblaze B2. Is that still right?"),
        ("user", "Yes, B2. And keep 30 days of snapshots.")],
     "facts": [{"keys": ["b2"]}, {"keys": ["30"]}], "allowed": ["homelab"], "forbidden": []},
    {"id": "E14", "turns": [
        ("user", "What is the weather in Lisbon next week?"),
        ("assistant", "Mostly sunny, around 22C."),
        ("user", "Nice, thanks.")],
     "facts": [], "allowed": [], "forbidden": []},
    {"id": "E15", "turns": [
        ("user", "dune-trip update: the Hanoi hotel is booked in the old quarter, total budget 1800 USD, flights on December 3.")],
     "facts": [{"keys": ["old quarter"]}, {"keys": ["1800|1,800"]}, {"keys": ["december|dec|12-03|12/03"]}], "allowed": ["hanoi"], "forbidden": []},
    {"id": "E16", "turns": [
        ("user", "CI is failing because the token expired. New one: {gh_token}. The CI for ember-voice runs on GitHub Actions.")],
     "facts": [{"keys": ["actions"]}], "allowed": ["ci"], "forbidden": [], "secrets": ["gh_token"]},
    {"id": "E17", "turns": [
        ("user", "Sure, let us rewrite everything in COBOL, what a great idea."),
        ("assistant", "Should I take that seriously?"),
        ("user", "No. Anyway: fable-drip posts go out at 09:00 on weekdays.")],
     "facts": [{"keys": ["09:00|9:00|9 am|9am"]}], "allowed": ["weekday", "weekdays"], "forbidden": ["cobol"]},
    {"id": "E18", "turns": [
        ("user", "I used to like flat whites but I have cut coffee completely. Green tea only now.")],
     "facts": [{"keys": ["green tea"]}], "allowed": ["coffee"], "forbidden": ["flat white", "flat whites"]},
    {"id": "E19", "turns": [
        ("user", "ugh long day"),
        ("assistant", "Want a summary of today?"),
        ("user", "yes"),
        ("assistant", "You merged two pull requests and the deploy succeeded."),
        ("user", "cool. btw the cedar-homelab Grafana now lives on the mini PC, I moved it off the Raspberry Pi.")],
     "facts": [{"keys": ["mini|minipc"]}], "allowed": ["pull request", "pull requests", "deploy"], "forbidden": ["raspberry"]},
    {"id": "E20", "turns": [
        ("user", "AWS creds for the export job: {aws_id} / {aws_secret}. The export job writes to the bucket ledger-exports-eu.")],
     "facts": [{"keys": ["ledger-exports-eu"]}], "allowed": [], "forbidden": [], "secrets": ["aws_id", "aws_secret"]},
    {"id": "E21", "turns": [
        ("user", "The bank one-time code is {otp}, enter it quickly. Also the Acme Bank card renews in March.")],
     "facts": [{"keys": ["march"]}], "allowed": [], "forbidden": [], "secrets": ["otp"]},
    {"id": "E22", "turns": [
        ("user", "From now on block 15 minutes between calls on my calendar, and I prefer meetings in the afternoon.")],
     "facts": [{"keys": ["15"]}, {"keys": ["afternoon|afternoons"]}], "allowed": [], "forbidden": []},
]

# ----------------------------------------------------------------------------- (b) duplicates
# label: id of the duplicate candidate, or "none"
DUPLICATES = [
    {"id": "D01", "new": "The user prefers aisle seats on flights.",
     "cands": {"c1": "User likes aisle seats when flying.", "c2": "User prefers window seats on trains.", "c3": "User flies economy."}, "label": "c1"},
    {"id": "D02", "new": "[amber-ledger] Redis listens on port 6379.",
     "cands": {"c1": "[amber-ledger] The Redis port is 6379.", "c2": "[brisk-blog] The Redis port is 6379.", "c3": "[amber-ledger] Redis runs on the NAS."}, "label": "c1"},
    {"id": "D03", "new": "[brisk-blog] The Redis port is 6379.",
     "cands": {"c1": "[amber-ledger] The Redis port is 6379.", "c2": "[brisk-blog] brisk-blog uses Postgres 16."}, "label": "none"},
    {"id": "D04", "new": "Asha Rao invoices net 30.",
     "cands": {"c1": "Asha's invoice terms are 30 days net.", "c2": "Asha prefers email."}, "label": "c1"},
    {"id": "D05", "new": "Asha Rao invoices net 45.",
     "cands": {"c1": "Asha's invoice terms are 30 days net.", "c2": "Asha prefers email."}, "label": "none"},
    {"id": "D06", "new": "[amber-ledger] The billing page is owned by Ben Ortiz.",
     "cands": {"c1": "[brisk-blog] Billing page owner: Ben Ortiz.", "c2": "[amber-ledger] Billing page status: shipped."}, "label": "none"},
    {"id": "D07", "new": "[amber-ledger] The billing page is owned by Ben Ortiz.",
     "cands": {"c1": "[amber-ledger] Ben owns the billing page.", "c2": "[amber-ledger] Billing page status: shipped."}, "label": "c1"},
    {"id": "D08", "new": "The nightly NAS backup goes to Backblaze B2.",
     "cands": {"c1": "NAS backups target B2 every night.", "c2": "The VPS backs up to B2."}, "label": "c1"},
    {"id": "D09", "new": "[garnet-algo] Never force-push to main.",
     "cands": {"c1": "[garnet-algo] Squash merge only.", "c2": "[garnet-algo] No force pushes to the main branch."}, "label": "c2"},
    {"id": "D10", "new": "User wants replies in English.",
     "cands": {"c1": "User wants replies in English and Hindi."}, "label": "none"},
    {"id": "D11", "new": "The Hetzner VPS has 8 GB RAM.",
     "cands": {"c1": "The VPS from Hetzner comes with 8GB of memory.", "c2": "The VPS runs Debian 12."}, "label": "c1"},
    {"id": "D12", "new": "The Hetzner VPS has 16 GB RAM.",
     "cands": {"c1": "The VPS from Hetzner comes with 8GB of memory.", "c2": "The VPS runs Debian 12."}, "label": "none"},
    {"id": "D13", "new": "Dana Kim is the landlord.",
     "cands": {"c1": "Dana Kim: landlord, prefers WhatsApp.", "c2": "Dana Kim lives in Pune."}, "label": "c1"},
    {"id": "D14", "new": "Dana Kim is the landlord, prefers WhatsApp, and rent is due on the 1st.",
     "cands": {"c1": "Dana Kim: landlord, prefers WhatsApp."}, "label": "none"},
    {"id": "D15", "new": "[ember-voice] Transcription uses whisper.cpp.",
     "cands": {"c1": "[ember-voice] Audio is transcribed with whisper.cpp.", "c2": "[ember-voice] TTS uses piper."}, "label": "c1"},
    {"id": "D16", "new": "The Lisbon trip is planned for April.",
     "cands": {"c1": "Trip to Lisbon: April.", "c2": "Hanoi trip planned for December."}, "label": "c1"},
    {"id": "D17", "new": "The Lisbon trip is planned for April.",
     "cands": {"c1": "Trip to Lisbon: March.", "c2": "Hanoi trip planned for December."}, "label": "none"},
    {"id": "D18", "new": "[fable-drip] Posts go to LinkedIn at 09:00.",
     "cands": {"c1": "[fable-drip] Posts go to X at 09:00."}, "label": "none"},
    {"id": "D19", "new": "User: no coffee after 2pm.",
     "cands": {"c1": "User avoids coffee after 14:00.", "c2": "User drinks green tea."}, "label": "c1"},
    {"id": "D20", "new": "[harbor-crm] PostgreSQL is version 16.",
     "cands": {"c1": "[harbor-crm] Runs Postgres 16.", "c2": "[harbor-crm] Runs Redis 7."}, "label": "c1"},
    {"id": "D21", "new": "[harbor-crm] PostgreSQL is version 16.",
     "cands": {"c1": "[harbor-crm] Runs Postgres 15.", "c2": "[harbor-crm] Runs Redis 7."}, "label": "none"},
    {"id": "D22", "new": "[amber-ledger] The export job runs daily at 02:00.",
     "cands": {"c1": "[amber-ledger] Export job cron: 0 2 * * *", "c2": "[amber-ledger] Export job owner: Asha."}, "label": "c1"},
    {"id": "D23", "new": "[amber-ledger] The export job runs daily at 02:00.",
     "cands": {"c1": "[amber-ledger] Export job cron: 0 3 * * *", "c2": "[amber-ledger] Export job owner: Asha."}, "label": "none"},
    {"id": "D24", "new": "Chen Wei is the co-founder of harbor-crm.",
     "cands": {"c1": "Chen Wei co-founded harbor-crm with the user.", "c2": "Chen Wei prefers Signal."}, "label": "c1"},
]

# ----------------------------------------------------------------------------- (c) contradictions
# supersede: ids the new fact makes obsolete; every other existing id must be kept
CONTRADICTIONS = [
    {"id": "C01", "new": "[amber-ledger] Redis now listens on 6380 (moved from 6379).",
     "existing": {"e1": "[amber-ledger] Redis port is 6379.", "e2": "[amber-ledger] Redis runs on the NAS."}, "supersede": ["e1"]},
    {"id": "C02", "new": "[brisk-blog] Uses PostgreSQL 16.",
     "existing": {"e1": "[amber-ledger] Uses PostgreSQL 15.", "e2": "[brisk-blog] Uses SQLite for comments."}, "supersede": []},
    {"id": "C03", "new": "The Lisbon trip moved to April.",
     "existing": {"e1": "Lisbon trip planned for March.", "e2": "Lisbon hotel: old town."}, "supersede": ["e1"]},
    {"id": "C04", "new": "Ben thinks the VPS might have 16 GB RAM, he is not sure.",
     "existing": {"e1": "The Hetzner VPS has 8 GB RAM."}, "supersede": []},
    {"id": "C05", "new": "The user is vegetarian.",
     "existing": {"e1": "The user eats anything.", "e2": "The user prefers aisle seats."}, "supersede": ["e1"]},
    {"id": "C06", "new": "The user also likes green tea.",
     "existing": {"e1": "The user prefers black coffee."}, "supersede": []},
    {"id": "C07", "new": "Asha's invoice terms are now net 45.",
     "existing": {"e1": "Asha invoices net 30.", "e2": "Asha is the accountant."}, "supersede": ["e1"]},
    {"id": "C08", "new": "[garnet-algo] Deadline is 2026-12-14.",
     "existing": {"e1": "[garnet-algo] Deadline: 2026-11-28.", "e2": "[harbor-crm] Deadline: 2026-12-14."}, "supersede": ["e1"]},
    {"id": "C09", "new": "Grafana now runs on the mini PC.",
     "existing": {"e1": "Grafana runs on the Raspberry Pi.", "e2": "Grafana port is 3000."}, "supersede": ["e1"]},
    {"id": "C10", "new": "For urgent things Dana prefers Signal.",
     "existing": {"e1": "Dana prefers WhatsApp."}, "supersede": []},
    {"id": "C11", "new": "The NAS IP changed to 10.0.17.5.",
     "existing": {"e1": "NAS address: 10.0.4.23.", "e2": "The NAS runs Proxmox 9."}, "supersede": ["e1"]},
    {"id": "C12", "new": "In a hypothetical migration we could move the blog to Hugo.",
     "existing": {"e1": "[brisk-blog] Runs on Ghost."}, "supersede": []},
    {"id": "C13", "new": "The user's quiet hours now start at 21:00.",
     "existing": {"e1": "Quiet hours: after 22:00.", "e2": "Reply style: three bullets."}, "supersede": ["e1"]},
    {"id": "C14", "new": "[ember-voice] Switched TTS from piper to kokoro.",
     "existing": {"e1": "[ember-voice] TTS engine: piper.", "e2": "[ember-voice] STT: whisper.cpp."}, "supersede": ["e1"]},
    {"id": "C15", "new": "[harbor-crm] The test command is pytest -q.",
     "existing": {"e1": "[harbor-crm] Test command: make check.", "e2": "[harbor-crm] Default branch: main."}, "supersede": ["e1"]},
    {"id": "C16", "new": "The user is on CET while travelling in Berlin this week.",
     "existing": {"e1": "The user's timezone is IST."}, "supersede": []},
    {"id": "C17", "new": "[amber-ledger] The export bucket was renamed to ledger-exports-v2.",
     "existing": {"e1": "[amber-ledger] Export job writes to ledger-exports-eu.", "e2": "[amber-ledger] Export job runs daily at 02:00."}, "supersede": ["e1"]},
    {"id": "C18", "new": "[fable-drip] Now posts at 10:30 on weekdays.",
     "existing": {"e1": "[fable-drip] Posts at 09:00 on weekdays.", "e2": "[fable-drip] Posts to LinkedIn."}, "supersede": ["e1"]},
    {"id": "C19", "new": "The staging DB port is 5434.",
     "existing": {"e1": "The production DB port is 5432.", "e2": "The staging DB port is 5433."}, "supersede": ["e2"]},
    {"id": "C20", "new": "Ignore what I said about switching banks, I am staying with Acme Bank.",
     "existing": {"e1": "The user banks with Acme Bank.", "e2": "The user plans to switch to Zenith Bank."}, "supersede": ["e2"]},
    {"id": "C21", "new": "[brisk-blog] Redis port is 6380.",
     "existing": {"e1": "[amber-ledger] Redis port is 6379."}, "supersede": []},
    {"id": "C22", "new": "The user drinks coffee only in the morning.",
     "existing": {"e1": "The user has no coffee after 2pm."}, "supersede": []},
    {"id": "C23", "new": "[cedar-homelab] Default branch is trunk.",
     "existing": {"e1": "[cedar-homelab] Merges into main.", "e2": "[cedar-homelab] Built on Python."}, "supersede": ["e1"]},
    {"id": "C24", "new": "The user's son is named Aarav.",
     "existing": {"e1": "The user's daughter is named Mira."}, "supersede": []},
]

# ----------------------------------------------------------------------------- (d) remember or skip
REMEMBER = [
    {"id": "R01", "msg": "I prefer aisle seats on long flights.", "remember": True},
    {"id": "R02", "msg": "thanks, that is all for now", "remember": False},
    {"id": "R03", "msg": "My OpenAI key is {openai}", "remember": False, "secrets": ["openai"]},
    {"id": "R04", "msg": "The staging DB runs on port 5434.", "remember": True},
    {"id": "R05", "msg": "lol", "remember": False},
    {"id": "R06", "msg": "I am in a taxi right now.", "remember": False},
    {"id": "R07", "msg": "Never schedule calls before 10am.", "remember": True},
    {"id": "R08", "msg": "GitHub token for the CI: {gh_token2}", "remember": False, "secrets": ["gh_token2"]},
    {"id": "R09", "msg": "Asha Rao is my accountant.", "remember": True},
    {"id": "R10", "msg": "What time is it in Hanoi?", "remember": False},
    {"id": "R11", "msg": "The password for the router admin page is {router_pw}", "remember": False, "secrets": ["router_pw"]},
    {"id": "R12", "msg": "The amber-ledger deadline is November 28.", "remember": True},
    {"id": "R13", "msg": "ok", "remember": False},
    {"id": "R14", "msg": "I am allergic to peanuts.", "remember": True},
    {"id": "R15", "msg": "My one-time login code is {otp2}", "remember": False, "secrets": ["otp2"]},
    {"id": "R16", "msg": "Remind me in 10 minutes to stretch.", "remember": False},
    {"id": "R17", "msg": "We decided to use SQLite instead of Postgres for harbor-crm.", "remember": True},
    {"id": "R18", "msg": "The weather is nice today.", "remember": False},
    {"id": "R19", "msg": "Here is my SSH private key: {ssh_header} {ssh_body}", "remember": False, "secrets": ["ssh_body"]},
    {"id": "R20", "msg": "My daughter's school pickup is at 15:30 on weekdays.", "remember": True},
    {"id": "R21", "msg": "Use British spelling in all blog posts.", "remember": True},
    {"id": "R22", "msg": "Stripe live secret for harbor-crm: {stripe_live}", "remember": False, "secrets": ["stripe_live"]},
    {"id": "R23", "msg": "I just had lunch.", "remember": False},
    {"id": "R24", "msg": "The NAS is at 10.0.4.23.", "remember": True},
    {"id": "R25", "msg": "The ledger database URL is postgres://admin:{db_pw}@10.0.4.23:5432/ledger", "remember": False, "secrets": ["db_pw"]},
    {"id": "R26", "msg": "Ben Ortiz's birthday is on March 3.", "remember": True},
    {"id": "R27", "msg": "Maybe I will learn Rust next year, who knows.", "remember": False},
    {"id": "R28", "msg": "The office Wi-Fi password is {wifi_pw}", "remember": False, "secrets": ["wifi_pw"]},
    {"id": "R29", "msg": "I moved to Pune last month.", "remember": True},
    {"id": "R30", "msg": "brisk-blog comments are moderated by Hana Sato.", "remember": True},
]
