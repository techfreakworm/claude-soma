#!/usr/bin/env python3
"""Seeded synthetic memory corpus + ground-truth oracle for the opencode-soma memory benchmark.

    python3.12 gen_memory.py --n-facts 600 --seed 7 --out DIR

Writes DIR/dataset-<n>.json (scopes, entities, sources, facts) and DIR/truth-<n>.json
(query instances and the expected answer for each, computed by the oracle at the
bottom of this file, which shares no code with the backends in bench.py).

Everything is derived from random.Random(seed); no set/dict iteration order leaks into
the output, so the same (n, seed) always yields byte-identical JSON (sha256 printed).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import random
import sys
from datetime import datetime, timezone

NOW_TS = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
DAY = 86400
WINDOW_DAYS = 180
START_TS = NOW_TS - WINDOW_DAYS * DAY
HALF_LIFE_DAYS = {"user": 180.0, "feedback": 120.0, "project": 30.0, "reference": 90.0}
HUB_DEGREE = 100
TOP_K = 10
GENERATOR_VERSION = "1"

SHARED_ENTITIES = [
    ("e-postgres", "PostgreSQL", "tool", ["Postgres", "pg"]),
    ("e-redis", "Redis", "tool", []),
    ("e-docker", "Docker", "tool", []),
    ("e-playwright", "Playwright", "tool", []),
    ("e-ollama", "ollama", "tool", []),
    ("e-caddy", "Caddy", "tool", []),
    ("e-sqlite", "SQLite", "tool", []),
    ("e-tmux", "tmux", "tool", []),
    ("e-ffmpeg", "ffmpeg", "tool", []),
    ("e-whisper", "whisper.cpp", "tool", ["whisper"]),
    ("e-grafana", "Grafana", "tool", []),
    ("e-nginx", "nginx", "tool", []),
    ("e-node", "Node.js", "tool", ["Node"]),
    ("e-python", "Python", "tool", []),
    ("e-tailscale", "Tailscale", "tool", []),
    ("e-restic", "restic", "tool", []),
    ("e-github", "GitHub", "service", []),
    ("e-telegram", "Telegram", "service", []),
    ("e-stripe", "Stripe", "service", []),
    ("e-cloudflare", "Cloudflare", "service", []),
    ("e-b2", "Backblaze B2", "service", ["B2"]),
    ("e-gmail", "Gmail", "service", []),
    ("e-gcal", "Google Calendar", "service", ["the calendar"]),
    ("e-x", "X", "service", ["Twitter"]),
    ("e-linkedin", "LinkedIn", "service", []),
    ("e-hetzner", "Hetzner", "service", []),
    ("e-asha", "Asha Rao", "person", ["Asha"]),
    ("e-ben", "Ben Ortiz", "person", ["Ben"]),
    ("e-chen", "Chen Wei", "person", ["Chen"]),
    ("e-dana", "Dana Kim", "person", ["Dana"]),
    ("e-eli", "Eli Novak", "person", ["Eli"]),
    ("e-farah", "Farah Iqbal", "person", ["Farah"]),
    ("e-goran", "Goran Petrov", "person", ["Goran"]),
    ("e-hana", "Hana Sato", "person", ["Hana"]),
    ("e-pune", "Pune", "place", []),
    ("e-lisbon", "Lisbon", "place", []),
    ("e-hanoi", "Hanoi", "place", []),
    ("e-berlin", "Berlin", "place", []),
    ("e-danang", "Da Nang", "place", ["Danang"]),
    ("e-nas", "homelab NAS", "host", ["NAS"]),
    ("e-minipc", "mini PC", "host", ["minipc"]),
    ("e-laptop", "work laptop", "host", ["laptop"]),
    ("e-vps", "Hetzner VPS", "host", ["VPS"]),
    ("e-pi", "Raspberry Pi", "host", ["Pi"]),
    ("e-bank", "Acme Bank", "org", []),
    ("e-insurer", "Zenith Insurance", "org", ["Zenith"]),
    ("e-user", "the user", "user", []),
]

BASE_LEADS = ["amber-ledger", "brisk-blog", "cedar-homelab", "dune-trip",
              "ember-voice", "fable-drip", "garnet-algo", "harbor-crm"]
ADJ = ["indigo", "jade", "kestrel", "lumen", "maple", "nimbus", "onyx", "pebble", "quartz",
       "raven", "sable", "tidal", "umber", "velvet", "willow", "xenon", "yarrow", "zephyr",
       "amber", "brisk", "cedar", "dune", "ember", "fable", "garnet", "harbor"]
NOUN = ["atlas", "forge", "garden", "kiosk", "lens", "mint", "notes", "orbit", "pantry", "quill",
        "radar", "studio", "tutor", "vault", "wiki", "yard", "ledger", "blog", "homelab", "trip",
        "voice", "drip", "algo", "crm"]
COMPONENTS = ["ingest worker", "billing page", "price feed", "login flow", "nightly report",
              "search index", "upload API", "settings page", "export job", "webhook handler",
              "rate limiter", "cache layer", "mobile layout", "onboarding email", "PDF renderer",
              "image pipeline", "cron runner", "admin panel", "audit log", "retry queue",
              "sync daemon", "feature flags", "health check", "sitemap builder", "draft queue",
              "voice clone", "trade journal", "invoice parser", "map view", "budget sheet"]

MONTHS = ["November", "December", "January", "February", "March", "April", "May", "June"]
STATUS = ["active", "paused", "blocked", "shipped", "in review"]

# attribute -> (phrase used in text, noun used in text, value kind)
ATTRS: dict[str, list[tuple[str, str, str, str]]] = {
    "tool": [("version", "is pinned to version", "version", "num"),
             ("port", "listens on port", "port", "num"),
             ("runs_on", "runs on the", "host", "host"),
             ("config_path", "reads its config from", "config path", "path"),
             ("backup_schedule", "is backed up", "backup schedule", "sched")],
    "service": [("plan", "is on the plan", "plan", "plan"),
                ("account_owner", "has its account owned by", "account owner", "person"),
                ("billing_day", "bills on day", "billing day", "day"),
                ("rate_limit", "allows", "rate limit", "rate")],
    "person": [("role", "works with us as", "role", "role"),
               ("contact_channel", "prefers to be reached by", "contact channel", "chan"),
               ("meeting_slot", "meets on", "meeting slot", "slot"),
               ("timezone", "keeps the timezone", "timezone", "tz"),
               ("invoice_terms", "invoices on terms", "invoice terms", "terms")],
    "place": [("visit_month", "is planned for", "visit month", "month"),
              ("budget_usd", "has a budget in dollars of", "budget", "budget"),
              ("stay_area", "should be booked in the", "stay area", "area")],
    "host": [("ip", "has the address", "address", "ip"),
             ("os", "runs the system", "operating system", "os"),
             ("ram_gb", "has memory in GB of", "memory size", "ram"),
             ("location", "sits in", "location", "place"),
             ("backup_target", "backs up to", "backup target", "target")],
    "org": [("account_type", "holds the account type", "account type", "acct"),
            ("renewal_month", "renews in", "renewal month", "month"),
            ("support_channel", "is supported via", "support channel", "chan")],
    "user": [("editor", "prefers the editor", "editor", "editor"),
             ("meeting_time", "prefers meetings in the", "meeting time", "mtime"),
             ("diet", "eats", "diet", "diet"),
             ("alarm_time", "wakes up at", "alarm time", "alarm"),
             ("language", "wants replies in", "reply language", "lang"),
             ("news_digest_time", "wants the news digest at", "digest time", "alarm"),
             ("reply_style", "wants", "reply style", "style"),
             ("currency", "budgets in", "currency", "cur")],
    "project": [("status", "is currently", "status", "status"),
                ("deadline", "is due on", "deadline", "date"),
                ("default_branch", "merges into", "default branch", "branch"),
                ("stack", "is built on", "stack", "tool"),
                ("owner", "is owned by", "owner", "person"),
                ("test_command", "is tested with", "test command", "test")],
    "component": [("owner", "is owned by", "owner", "person"),
                  ("status", "is currently", "status", "status"),
                  ("cron", "is scheduled as", "cron schedule", "cron"),
                  ("depends_on", "depends on", "dependency", "tool"),
                  ("flag", "is", "rollout flag", "flag")],
}
RULES = {
    "rule-git": ["never force-push to main", "squash merge only", "rebase before merging"],
    "rule-deploy": ["deploy only after the smoke test", "no deploys on Friday", "deploy with a dry run first"],
    "rule-tests": ["run the full suite before commit", "add a failing test first", "keep tests offline"],
    "rule-secrets": ["keep secrets in the env file", "never paste tokens into chat", "rotate keys monthly"],
    "rule-style": ["keep functions short", "no emoji in commits", "prefer plain SQL"],
}
USER_PREFS = {
    "coffee": ["black coffee", "flat white", "no coffee after 2pm", "oat milk latte"],
    "seat": ["aisle seat", "window seat", "exit row"],
    "hotel": ["quiet rooms away from the lift", "apartments with a kitchen", "small guesthouses"],
    "music": ["lo-fi", "no music", "jazz", "film scores"],
    "gym": ["06:30", "18:00", "weekends only"],
    "quiet hours": ["after 21:00", "after 22:00", "only urgent after 20:00"],
    "calendar buffer": ["15 minutes between calls", "30 minutes between calls", "no buffer"],
    "travel class": ["economy", "premium economy", "business on long haul"],
    "summary length": ["three bullets", "one paragraph", "a short voice note"],
    "code review": ["inline comments", "a single summary", "a checklist"],
    "tea": ["masala chai", "green tea", "no tea"],
    "commute": ["metro", "bike", "cab"],
    "weekends": ["hiking", "family time", "no work messages"],
    "photo sync": ["weekly", "after every trip", "monthly"],
    "reading": ["papers first", "books first", "newsletters on Sunday only"],
}
TOPICS = ["backup", "deploy", "invoice", "migration", "certificate", "refund", "booking",
          "dashboard", "rollback", "payment", "sync", "upload", "report", "release", "export"]
VERBS = ["failed", "succeeded", "was delayed", "was retried", "was cancelled", "needed a manual fix"]
CAUSES = ["an expired token", "a full disk", "a DNS change", "a rate limit", "a schema change",
          "a typo in the config", "a network outage", "a missing permission", "a price change",
          "an upstream bug"]
Q8_PAIRS = [("backup", "token"), ("deploy", "dns"), ("migration", "schema"),
            ("invoice", "permission"), ("rollback", "typo"), ("refund", "price"),
            ("sync", "outage"), ("release", "upstream"), ("upload", "disk"),
            ("payment", "limit"), ("report", "token"), ("export", "schema")]
CTX = ["(from the {t} thread)", "(seen during the {t} work)", "(raised after the {t} review)"]


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def lead_names(n: int) -> list[str]:
    names = list(BASE_LEADS)
    for a in ADJ:
        for b in NOUN:
            nm = f"{a}-{b}"
            if nm not in names:
                names.append(nm)
    return names[:n]


class Gen:
    def __init__(self, n_facts: int, seed: int) -> None:
        self.n = n_facts
        self.seed = seed
        self.rng = random.Random(seed)
        self.n_leads = max(8, n_facts // 400)
        self.leads = [f"lead:{x}" for x in lead_names(self.n_leads)]
        self.scopes = ["parent"] + self.leads
        self.entities: dict[str, dict] = {}
        self.order_entities: list[str] = []
        self.sources: list[dict] = []
        self.facts: list[dict] = []
        self.used_keys: set[tuple[str, str, str]] = set()
        self.event_no = 0
        self.planted = {"duplicates": 0, "contradictions_unresolved": 0,
                        "contradictions_resolved": 0, "updates": 0, "update_chains": 0,
                        "derived": 0, "promoted": 0, "alias_mentions": 0}

    # ---------------------------------------------------------------- entities
    def add_entity(self, eid: str, name: str, kind: str, aliases: list[str], scope: str | None) -> None:
        self.entities[eid] = {"id": eid, "name": name, "kind": kind, "aliases": aliases, "home_scope": scope}
        self.order_entities.append(eid)

    def build_entities(self) -> None:
        for eid, name, kind, aliases in SHARED_ENTITIES:
            self.add_entity(eid, name, kind, list(aliases), None)
        self.scope_pool: dict[str, list[str]] = {}
        self.project_entity: dict[str, str] = {}
        shared_ids = [e[0] for e in SHARED_ENTITIES if e[2] != "user"]
        for lead in self.leads:
            short = lead.split(":", 1)[1]
            pid = f"e-proj-{short}"
            self.add_entity(pid, short, "project", [], lead)
            self.project_entity[lead] = pid
            n_comp = 10 + (self.n // self.n_leads) // 60
            comps = self.rng.sample(COMPONENTS, min(n_comp, len(COMPONENTS)))
            local = []
            for c in comps:
                cid = f"e-{short}-{slugify(c)}"
                self.add_entity(cid, c, "component", [], lead)
                local.append(cid)
            shared_pick = self.rng.sample(shared_ids, 22)
            self.scope_pool[lead] = [pid] * 3 + local * 2 + shared_pick
        parent_pool = ["e-user"] * 20 + [e[0] for e in SHARED_ENTITIES if e[2] in ("person", "service", "org", "place")]
        parent_pool += list(self.project_entity.values())
        self.scope_pool["parent"] = parent_pool

    def mention(self, eid: str, allow_alias: bool = True) -> str:
        e = self.entities[eid]
        if allow_alias and e["aliases"] and self.rng.random() < 0.4:
            self.planted["alias_mentions"] += 1
            return self.rng.choice(e["aliases"])
        return e["name"]

    # ---------------------------------------------------------------- sources
    def build_sources(self) -> None:
        per_scope = {s: 0 for s in self.scopes}
        weights = self.scope_weights()
        for s, w in zip(self.scopes, weights):
            per_scope[s] = max(1, int(self.n * w / sum(weights)))
        self.sessions: dict[str, list[tuple[int, dict]]] = {}
        tg_no = 50000
        ses_no = 0
        tool_no = 0
        file_no = 0
        for scope in self.scopes:
            n_ses = max(4, per_scope[scope] // 5)
            starts = sorted([START_TS] + [self.rng.randint(START_TS, NOW_TS - 3600) for _ in range(n_ses - 1)])
            lst = []
            for st in starts:
                root = None
                if scope != "parent" and self.rng.random() < 0.5 and self.sessions.get("parent"):
                    ps = self.sessions["parent"]
                    i = bisect.bisect_right([x[0] for x in ps], st) - 1
                    if i >= 0:
                        root = ps[i][1]
                if root is None:
                    tg_no += self.rng.randint(1, 40)
                    root = {"id": f"tg-{tg_no}", "kind": "telegram", "ref": f"chat 1001 message {tg_no}",
                            "parent_id": None, "created_at": st - self.rng.randint(5, 600)}
                    self.sources.append(root)
                ses_no += 1
                ses = {"id": f"ses-{ses_no}", "kind": "session", "ref": f"{scope} session {ses_no}",
                       "parent_id": root["id"], "created_at": st}
                self.sources.append(ses)
                tools = []
                for k in range(self.rng.randint(3, 6)):
                    tool_no += 1
                    t = {"id": f"tool-{tool_no}", "kind": "tool",
                         "ref": f"{self.rng.choice(['bash', 'read', 'grep', 'webfetch', 'edit'])} call {k + 1}",
                         "parent_id": ses["id"], "created_at": st + 60 * (k + 1)}
                    self.sources.append(t)
                    f = None
                    if self.rng.random() < 0.4:
                        file_no += 1
                        f = {"id": f"file-{file_no}", "kind": "file",
                             "ref": f"notes/{self.rng.choice(['todo', 'plan', 'log', 'readme', 'config'])}-{file_no}.md",
                             "parent_id": t["id"], "created_at": st + 60 * (k + 1) + 5}
                        self.sources.append(f)
                    tools.append((t, f))
                ses["_tools"] = tools
                ses["_root"] = root
                lst.append((st, ses))
            self.sessions[scope] = lst
        self.source_by_id = {s["id"]: s for s in self.sources}

    def scope_weights(self) -> list[float]:
        return [1.4] + [1.0 for _ in self.leads]

    def pick_source(self, scope: str, ts: int) -> str:
        lst = self.sessions[scope]
        i = max(0, bisect.bisect_right([x[0] for x in lst], ts) - 1)
        ses = lst[i][1]
        r = self.rng.random()
        if scope == "parent":
            return ses["_root"]["id"] if r < 0.6 else ses["id"]
        if r < 0.2 and ses["_root"]["kind"] == "telegram":
            return ses["_root"]["id"]
        if r < 0.55:
            return ses["id"]
        t, f = self.rng.choice(ses["_tools"])
        if r > 0.85 and f is not None:
            return f["id"]
        return t["id"]

    # ---------------------------------------------------------------- values
    def attr_spec(self, kind: str, attr: str) -> tuple[str, str, str]:
        if attr.startswith("rule-"):
            return "", attr.split("-", 1)[1], "rule"
        if attr.startswith("pref-"):
            topic = [t for t in USER_PREFS if "pref-" + slugify(t) == attr][0]
            return f"has this preference on {topic}:", f"{topic} preference", "pref:" + topic
        spec = [x for x in ATTRS[kind] if x[0] == attr][0]
        return spec[1], spec[2], spec[3]

    def value_for(self, vkind: str, scope: str) -> tuple[str, str, str | None]:
        r = self.rng
        if vkind.startswith("pref:"):
            v = r.choice(USER_PREFS[vkind.split(":", 1)[1]])
            return v, v, None
        if vkind == "num":
            v = str(r.randint(3, 9999))
            return v, v, None
        if vkind == "host":
            e = r.choice(["e-nas", "e-minipc", "e-laptop", "e-vps", "e-pi"])
            return e, self.mention(e), e
        if vkind == "person":
            e = r.choice(["e-asha", "e-ben", "e-chen", "e-dana", "e-eli", "e-farah", "e-goran", "e-hana"])
            return e, self.mention(e), e
        if vkind == "place":
            e = r.choice(["e-pune", "e-lisbon", "e-hanoi", "e-berlin", "e-danang"])
            return e, self.mention(e), e
        if vkind == "target":
            e = r.choice(["e-b2", "e-nas", "e-vps", "e-github"])
            return e, self.mention(e), e
        if vkind == "tool":
            e = r.choice(["e-postgres", "e-redis", "e-sqlite", "e-node", "e-python", "e-docker", "e-ollama", "e-caddy"])
            return e, self.mention(e), e
        table = {
            "path": [f"/etc/app/{x}.toml" for x in ("main", "prod", "local", "edge", "batch")],
            "sched": ["nightly at 02:00", "every 6 hours", "weekly on Sunday", "hourly", "daily at 23:30"],
            "plan": ["free", "pro", "team", "starter", "business"],
            "day": [str(d) for d in range(1, 29)],
            "rate": [f"{x} requests per minute" for x in (30, 60, 100, 300, 1000)],
            "role": ["accountant", "designer", "co-founder", "landlord", "dentist", "mentor", "contractor", "editor"],
            "chan": ["email", "phone call", "WhatsApp", "Signal", "SMS"],
            "slot": ["Monday 09:30", "Tuesday 14:00", "Wednesday 11:00", "Thursday 16:30", "Friday 10:00"],
            "tz": ["IST", "UTC", "CET", "ICT", "PST", "JST"],
            "terms": ["net 15", "net 30", "due on receipt", "net 45"],
            "month": MONTHS,
            "budget": [str(x) for x in range(400, 4000, 50)],
            "area": ["old town", "riverside", "station quarter", "city centre", "beach side"],
            "ip": [f"10.{a}.{b}.{c}" for a in range(3) for b in (4, 17, 88) for c in (5, 23, 140)],
            "os": ["Ubuntu 24.04", "Debian 12", "macOS 26", "Fedora 43", "Proxmox 9"],
            "ram": ["4", "8", "16", "32", "64", "128"],
            "acct": ["savings", "current", "credit card", "brokerage"],
            "editor": ["VS Code", "Neovim", "Zed", "Helix"],
            "mtime": ["mornings", "afternoons", "late afternoons", "evenings"],
            "diet": ["vegetarian food", "vegan food", "anything", "pescatarian food"],
            "alarm": ["06:00", "06:30", "07:00", "07:30", "08:00", "09:00", "18:00"],
            "lang": ["English", "Hindi", "English and Hindi"],
            "style": ["short bullet replies", "detailed answers", "voice notes", "one-line summaries"],
            "cur": ["INR", "USD", "EUR"],
            "status": STATUS,
            "date": [f"2026-{m:02d}-{d:02d}" for m in (10, 11, 12) for d in (3, 9, 14, 21, 28)],
            "branch": ["main", "develop", "trunk"],
            "test": ["pytest -q", "npm test", "make check", "bun test", "go test"],
            "cron": ["every 5 minutes", "daily at 02:00", "hourly", "Mondays at 06:30"],
            "flag": ["enabled", "disabled", "beta only", "rolled back"],
        }
        v = r.choice(table[vkind])
        return v, v, None

    def other_value(self, vkind: str, scope: str, current: str) -> tuple[str, str, str | None]:
        for _ in range(50):
            v = self.value_for(vkind, scope)
            if v[0] != current:
                return v
        raise RuntimeError(f"no alternative value for {vkind}")

    # ---------------------------------------------------------------- fact construction
    def scope_label(self, scope: str) -> str:
        return "the assistant" if scope == "parent" else scope.split(":", 1)[1]

    def fact_type(self, kind: str, attr: str) -> str:
        if attr.startswith("rule-"):
            return "feedback"
        if kind == "user":
            return "user"
        if kind in ("project", "component", "tool", "host"):
            return "project"
        return "reference"

    def visibility(self, scope: str, ftype: str) -> str:
        r = self.rng.random()
        if scope == "parent":
            if ftype in ("user", "feedback"):
                return "global" if r < 0.8 else "local"
            if r < 0.15:
                return self.rng.choice(self.leads)
            return "global" if r < 0.45 else "local"
        return "parent" if r < 0.78 else "local"

    def render_keyed(self, scope: str, subj: str, attr: str, phrase: str, noun: str, vtext: str,
                     variant: int, extra_entities: list[str]) -> tuple[str, list[str]]:
        s = self.mention(subj)
        p = self.scope_label(scope)
        ents = [subj]
        if scope != "parent" and self.project_entity[scope] != subj:
            ents.append(self.project_entity[scope])
        ctx = ""
        if self.rng.random() < 0.35:
            ctx = " " + self.rng.choice(CTX).format(t=self.rng.choice(TOPICS))
        if attr.startswith("rule-"):
            forms = [f"Rule for {p}: {vtext} ({noun}).",
                     f"In {p} the {noun} rule is: {vtext}.",
                     f"{p} follows this {noun} rule, {vtext}."]
        else:
            forms = [f"{p}: {s} {phrase} {vtext}.{ctx}",
                     f"Noted for {p} that {s} {phrase} {vtext}.{ctx}",
                     f"Remember in {p}: the {noun} of {s} is {vtext}.{ctx}"]
        text = forms[variant % 3]
        if scope == "parent" and p == "the assistant" and "e-user" not in ents and " the user" in f" {text}":
            ents.append("e-user")
        for x in extra_entities:
            if x not in ents:
                ents.append(x)
        return text, ents

    def new_fact(self, **kw) -> dict:
        f = {"id": None, "scope": kw["scope"], "type": kw["type"], "visibility": kw["visibility"],
             "subject": kw["subject"], "attribute": kw["attribute"], "value": kw["value"],
             "vkind": kw.get("vkind"), "text": kw["text"], "entities": kw["entities"],
             "importance": kw["importance"], "created_at": kw["created_at"],
             "source_id": kw["source_id"], "derived_from": kw.get("derived_from", []),
             "supersedes": [], "superseded_by": None, "_role": kw.get("role", "base")}
        self.facts.append(f)
        return f

    def keyed_fact(self, scope: str, ts: int) -> dict | None:
        pool = self.scope_pool[scope]
        for _ in range(12):
            subj = self.rng.choice(pool)
            kind = self.entities[subj]["kind"]
            if kind == "user" or (kind == "project" and self.rng.random() < 0.25):
                if self.rng.random() < 0.3:
                    attr = self.rng.choice(sorted(RULES))
                    if (scope, subj, attr) in self.used_keys:
                        continue
                    self.used_keys.add((scope, subj, attr))
                    v = self.rng.choice(RULES[attr])
                    noun = attr.split("-", 1)[1]
                    return self._mk_keyed(scope, ts, subj, attr, "", noun, "rule", (v, v, None), kind)
            if kind == "user" and self.rng.random() < 0.6:
                topic = self.rng.choice(sorted(USER_PREFS))
                attr = "pref-" + slugify(topic)
                phrase, noun, vkind = self.attr_spec(kind, attr)
            else:
                attr, phrase, noun, vkind = self.rng.choice(ATTRS[kind])
            if (scope, subj, attr) in self.used_keys:
                continue
            self.used_keys.add((scope, subj, attr))
            val = self.value_for(vkind, scope)
            return self._mk_keyed(scope, ts, subj, attr, phrase, noun, vkind, val, kind)
        return None

    def _mk_keyed(self, scope, ts, subj, attr, phrase, noun, vkind, val, kind) -> dict:
        value, vtext, vent = val
        extra = [vent] if vent else []
        text, ents = self.render_keyed(scope, subj, attr, phrase, noun, vtext, self.rng.randint(0, 2), extra)
        ftype = self.fact_type(kind, attr)
        return self.new_fact(scope=scope, type=ftype, visibility=self.visibility(scope, ftype), subject=subj,
                             attribute=attr, value=value, vkind=vkind, text=text, entities=ents,
                             importance=self.rng.choice([1, 2, 2, 3, 3, 3, 4, 4, 5]), created_at=ts,
                             source_id=self.pick_source(scope, ts))

    def episodic_fact(self, scope: str, ts: int) -> dict:
        self.event_no += 1
        subj = self.rng.choice(self.scope_pool[scope])
        topic = self.rng.choice(TOPICS)
        verb = self.rng.choice(VERBS)
        cause = self.rng.choice(CAUSES)
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        s = self.mention(subj)
        p = self.scope_label(scope)
        ents = [subj]
        if scope != "parent" and self.project_entity[scope] != subj:
            ents.append(self.project_entity[scope])
        text = f"On {day} the {topic} for {s} in {p} {verb} because of {cause}."
        if self.rng.random() < 0.3:
            other = self.rng.choice(self.scope_pool[scope])
            if other not in ents:
                text += f" Follow up with {self.mention(other)}."
                ents.append(other)
        ftype = "project" if scope != "parent" else "reference"
        return self.new_fact(scope=scope, type=ftype, visibility=self.visibility(scope, ftype), subject=subj,
                             attribute=f"event-{self.event_no}", value=f"{topic} {verb}", vkind=None,
                             text=text, entities=ents, importance=self.rng.choice([1, 1, 2, 2, 3, 4]),
                             created_at=ts, source_id=self.pick_source(scope, ts), role="episodic")

    def clone_with(self, base: dict, ts: int, value_triplet: tuple[str, str, str | None] | None, role: str) -> dict:
        """A later fact on the same (scope, subject, attribute): same value re-worded, or a new value."""
        scope, subj, attr = base["scope"], base["subject"], base["attribute"]
        kind = self.entities[subj]["kind"]
        phrase, noun, _ = self.attr_spec(kind, attr)
        if value_triplet is None:
            value = base["value"]
            vent = value if value.startswith("e-") else None
            vtext = self.mention(vent) if vent else value
        else:
            value, vtext, vent = value_triplet
        prev_variant = None
        text = None
        for _ in range(10):
            text, ents = self.render_keyed(scope, subj, attr, phrase, noun, vtext, self.rng.randint(0, 2),
                                           [vent] if vent else [])
            if text != base["text"]:
                break
        return self.new_fact(scope=scope, type=base["type"], visibility=base["visibility"], subject=subj,
                             attribute=attr, value=value, vkind=base["vkind"], text=text, entities=ents,
                             importance=base["importance"], created_at=ts, source_id=self.pick_source(scope, ts),
                             role=role)

    def later(self, ts: int, max_days: int = 60) -> int:
        lo = ts + 60
        hi = min(ts + max_days * DAY, NOW_TS - 1)
        if hi <= lo:
            return min(ts + 1, NOW_TS)
        return self.rng.randint(lo, hi)

    # ---------------------------------------------------------------- generation
    def generate(self) -> None:
        self.build_entities()
        self.build_sources()
        n = self.n
        n_dup = int(n * 0.07)
        n_unres = max(6, int(n * 0.025))
        n_res = max(6, int(n * 0.02))
        n_upd = int(n * 0.05)
        n_der = int(n * 0.05)
        n_prom = max(4, int(n * 0.015))
        n_extra = n_dup + n_unres + 2 * n_res + n_upd + (n_upd // 4) + n_der + n_prom
        n_base = n - n_extra
        weights = self.scope_weights()
        for _ in range(n_base):
            scope = self.rng.choices(self.scopes, weights=weights)[0]
            ts = self.rng.randint(START_TS + 3600, NOW_TS - 3600)
            f = None
            if self.rng.random() < 0.62:
                f = self.keyed_fact(scope, ts)
            if f is None:
                self.episodic_fact(scope, ts)
        keyed = [f for f in self.facts if f["_role"] == "base" and f["created_at"] < NOW_TS - 10 * DAY]
        self.rng.shuffle(keyed)
        cursor = 0

        def take(k: int) -> list[dict]:
            nonlocal cursor
            out = keyed[cursor:cursor + k]
            cursor += k
            if len(out) < k:
                raise RuntimeError("not enough keyed facts to plant")
            return out

        for b in take(n_dup):
            self.clone_with(b, self.later(b["created_at"]), None, "duplicate")
            self.planted["duplicates"] += 1
        self.unresolved_keys = []
        for b in take(n_unres):
            c = self.clone_with(b, self.later(b["created_at"]), self.other_value(b["vkind"], b["scope"], b["value"]) if b["vkind"] != "rule" else self._other_rule(b), "conflict")
            self.unresolved_keys.append((b["scope"], b["subject"], b["attribute"]))
            self.planted["contradictions_unresolved"] += 1
        for b in take(n_res):
            c = self.clone_with(b, self.later(b["created_at"], 30), self.other_value(b["vkind"], b["scope"], b["value"]) if b["vkind"] != "rule" else self._other_rule(b), "conflict")
            r = self.clone_with(c, self.later(c["created_at"], 30), None, "resolution")
            r["_supersedes_objs"] = [b, c]
            self.planted["contradictions_resolved"] += 1
        upd = take(n_upd)
        for i, b in enumerate(upd):
            c = self.clone_with(b, self.later(b["created_at"], 40), self.other_value(b["vkind"], b["scope"], b["value"]) if b["vkind"] != "rule" else self._other_rule(b), "update")
            c["_supersedes_objs"] = [b]
            self.planted["updates"] += 1
            if i < n_upd // 4:
                d = self.clone_with(c, self.later(c["created_at"], 20), self.other_value(c["vkind"], c["scope"], c["value"]) if c["vkind"] != "rule" else self._other_rule(c), "update")
                d["_supersedes_objs"] = [c]
                self.planted["update_chains"] += 1
        # derived (consolidated) facts, some built on earlier derived facts
        derived: list[dict] = []
        pool_by_scope: dict[str, list[dict]] = {}
        for f in self.facts:
            if f["_role"] in ("base", "episodic"):
                pool_by_scope.setdefault(f["scope"], []).append(f)
        scopes_sorted = [s for s in self.scopes if len(pool_by_scope.get(s, [])) >= 4]
        for i in range(n_der):
            scope = self.rng.choice(scopes_sorted)
            cands = pool_by_scope[scope]
            parents = self.rng.sample(cands, self.rng.randint(2, 3))
            prior = [d for d in derived if d["scope"] == scope]
            if prior and self.rng.random() < 0.35:
                parents[0] = self.rng.choice(prior)
            ts = self.later(max(p["created_at"] for p in parents), 20)
            ents: list[str] = []
            for p in parents:
                for e in p["entities"]:
                    if e not in ents:
                        ents.append(e)
            subj = parents[0]["subject"]
            text = f"Summary for {self.scope_label(scope)} about {self.mention(subj)}: " + " ".join(
                p["text"].rstrip(".") + "." for p in parents[:2])
            d = self.new_fact(scope=scope, type=parents[0]["type"], visibility=parents[0]["visibility"],
                              subject=subj, attribute=f"summary-{i + 1}", value="summary", vkind=None,
                              text=text, entities=ents[:8], importance=max(p["importance"] for p in parents),
                              created_at=ts, source_id=self.pick_source(scope, ts), role="derived")
            d["_derived_objs"] = parents
            derived.append(d)
            self.planted["derived"] += 1
        # promotion: a lead fact copied into the parent scope as a global fact
        lead_cands = [f for f in self.facts if f["scope"] != "parent" and f["visibility"] == "parent"
                      and f["_role"] == "base" and f["created_at"] < NOW_TS - 2 * DAY]
        for i, lf in enumerate(self.rng.sample(lead_cands, n_prom)):
            ts = self.later(lf["created_at"], 15)
            text = f"Promoted from {self.scope_label(lf['scope'])}: {lf['text']}"
            p = self.new_fact(scope="parent", type="user" if lf["type"] == "user" else "reference",
                              visibility="global", subject=lf["subject"], attribute=f"promoted-{i + 1}",
                              value="promoted", vkind=None, text=text, entities=list(lf["entities"]),
                              importance=lf["importance"], created_at=ts,
                              source_id=self.pick_source("parent", ts), role="promoted")
            p["_derived_objs"] = [lf]
            self.planted["promoted"] += 1
        self.finalize()

    def _other_rule(self, b: dict) -> tuple[str, str, None]:
        alts = [x for x in RULES[b["attribute"]] if x != b["value"]]
        v = self.rng.choice(alts)
        return v, v, None

    def finalize(self) -> None:
        self.facts.sort(key=lambda f: (f["created_at"], f["scope"], f["text"]))
        for i, f in enumerate(self.facts, 1):
            f["id"] = f"f-{i:06d}"
        for f in self.facts:
            for old in f.pop("_supersedes_objs", []):
                f["supersedes"].append(old["id"])
                old["superseded_by"] = f["id"]
            f["derived_from"] = [p["id"] for p in f.pop("_derived_objs", [])]
        for f in self.facts:
            f["entities"] = list(dict.fromkeys(f["entities"]))
            f["supersedes"].sort()
            subj = self.entities[f["subject"]]["name"]
            f["name"] = f"{subj} {f['attribute']}".replace("-", " ")
            f["description"] = f["text"][:90]
        for s in self.sources:
            s.pop("_tools", None)
            s.pop("_root", None)

    def dataset(self) -> dict:
        scopes = [{"id": "parent", "kind": "parent", "slug": "-home-op-opencode-soma"}]
        scopes += [{"id": l, "kind": "lead", "slug": f"-home-op-projects-{l.split(':', 1)[1]}"} for l in self.leads]
        facts = [{k: v for k, v in f.items() if not k.startswith("_") and k != "vkind"} for f in self.facts]
        used_src = {f["source_id"] for f in facts}
        keep: set[str] = set()
        by_id = {s["id"]: s for s in self.sources}
        for sid in sorted(used_src):
            while sid and sid not in keep:
                keep.add(sid)
                sid = by_id[sid]["parent_id"]
        sources = [s for s in self.sources if s["id"] in keep]
        return {"meta": {"generator_version": GENERATOR_VERSION, "seed": self.seed, "n_facts": len(facts),
                         "now_ts": NOW_TS, "window_days": WINDOW_DAYS, "half_life_days": HALF_LIFE_DAYS,
                         "hub_degree": HUB_DEGREE, "top_k": TOP_K, "planted": self.planted},
                "scopes": scopes,
                "entities": [{k: v for k, v in self.entities[e].items()} for e in self.order_entities],
                "sources": sources, "facts": facts}


# ==================================================================== oracle
# Reference semantics. bench.py re-implements these per backend; correctness is
# checked against what this oracle emits.

def o_active(f: dict) -> bool:
    return f["superseded_by"] is None


def o_visible_to_lead(f: dict, lead: str) -> bool:
    return o_active(f) and (f["scope"] == lead or (f["scope"] == "parent" and f["visibility"] in ("global", lead)))


def o_score(f: dict, now: int) -> float:
    age_days = (now - f["created_at"]) / 86400.0
    return f["importance"] * 0.5 ** (age_days / HALF_LIFE_DAYS[f["type"]])


def o_q1(facts, e):
    return sorted(f["id"] for f in facts if o_active(f) and e in f["entities"])


def o_q2(facts):
    by_e: dict[str, list[dict]] = {}
    for f in facts:
        if o_active(f) and f["scope"] != "parent":
            for e in f["entities"]:
                by_e.setdefault(e, []).append(f)
    out = {}
    for e in sorted(by_e):
        if len({f["scope"] for f in by_e[e]}) >= 2:
            out[e] = sorted(f["id"] for f in by_e[e])
    return out


def o_q3(facts, lead, now, k=TOP_K):
    vis = [f for f in facts if o_visible_to_lead(f, lead)]
    vis.sort(key=lambda f: (-o_score(f, now), f["id"]))
    return [f["id"] for f in vis[:k]]


def o_q4(facts, sources, fid, _cache={}):
    key = (id(facts), id(sources))
    if key not in _cache:
        _cache.clear()
        _cache[key] = ({f["id"]: f for f in facts}, {s["id"]: s for s in sources})
    fb, sb = _cache[key]
    seen_f: set[str] = set()
    stack = [fid]
    while stack:
        x = stack.pop()
        if x in seen_f:
            continue
        seen_f.add(x)
        stack.extend(fb[x]["derived_from"])
    seen_s: set[str] = set()
    for x in seen_f:
        s = fb[x]["source_id"]
        while s and s not in seen_s:
            seen_s.add(s)
            s = sb[s]["parent_id"]
    return {"facts": sorted(seen_f), "sources": sorted(seen_s)}


def o_q5(facts):
    groups: dict[tuple, list[dict]] = {}
    for f in facts:
        if o_active(f):
            groups.setdefault((f["scope"], f["subject"], f["attribute"]), []).append(f)
    out = []
    for key in sorted(groups):
        g = groups[key]
        if len({f["value"] for f in g}) > 1:
            out.append([key[0], key[1], key[2], sorted(f["id"] for f in g)])
    return out


def o_q6(facts, e):
    act = [f for f in facts if o_active(f)]
    deg: dict[str, int] = {}
    for f in act:
        for x in f["entities"]:
            deg[x] = deg.get(x, 0) + 1
    rel = {x for f in act if e in f["entities"] for x in f["entities"] if x != e and deg[x] <= HUB_DEGREE}
    return sorted(f["id"] for f in act if rel & set(f["entities"]))


def o_q7a(facts, lead):
    return sorted(f["id"] for f in facts if o_active(f) and f["scope"] == lead and f["visibility"] == "parent")


def o_q7b(facts, lead):
    return sorted(f["id"] for f in facts if o_active(f) and f["scope"] == "parent" and f["visibility"] in ("global", lead))


def o_q8(facts, lead, terms):
    return sorted(f["id"] for f in facts if o_visible_to_lead(f, lead) and all(t in tokens(f["text"]) for t in terms))


def build_truth(g: Gen, ds: dict) -> dict:
    facts, sources, now = ds["facts"], ds["sources"], NOW_TS
    rng = random.Random(g.seed * 1000 + 17)
    leads = [s["id"] for s in ds["scopes"] if s["kind"] == "lead"]
    lead_sample = sorted(rng.sample(leads, min(10, len(leads))))
    act = [f for f in facts if o_active(f)]
    deg: dict[str, int] = {}
    for f in act:
        for x in f["entities"]:
            deg[x] = deg.get(x, 0) + 1
    aliased = [e for e in ["e-postgres", "e-nas", "e-asha", "e-vps", "e-b2"] if deg.get(e)]
    shared_other = sorted(e for e in deg if not e.startswith(("e-proj-",)) and e in {x[0] for x in SHARED_ENTITIES}
                          and e not in aliased and e != "e-user")
    local = sorted(e for e in deg if g.entities[e]["kind"] == "component")
    q1_ents = aliased[:3] + rng.sample(shared_other, 3) + rng.sample(local, 4)
    derived = [f["id"] for f in facts if f["derived_from"]]
    deep = sorted(derived, key=lambda x: -len(o_q4(facts, sources, x)["facts"]))[:3]
    q4_ids = deep + rng.sample([d for d in derived if d not in deep], 3) + rng.sample(
        [f["id"] for f in facts if not f["derived_from"]], 4)
    q6_ents = rng.sample(local, 6) + rng.sample([e for e in shared_other if deg[e] >= 2], 4)
    q8 = []
    for i in range(10):
        lead = lead_sample[i % len(lead_sample)]
        pairs = list(Q8_PAIRS) if i < 5 else [(t,) for t in TOPICS]
        rng.shuffle(pairs)
        chosen = pairs[0]
        for p in pairs:
            if o_q8(facts, lead, list(p)):
                chosen = p
                break
        q8.append((lead, list(chosen)))
    truth = {
        "Q1": [{"params": {"entity": e}, "expected": o_q1(facts, e)} for e in q1_ents],
        "Q2": [{"params": {}, "expected": o_q2(facts)}],
        "Q3": [{"params": {"lead": l, "k": TOP_K, "now": now}, "expected": o_q3(facts, l, now)} for l in lead_sample],
        "Q4": [{"params": {"fact": x}, "expected": o_q4(facts, sources, x)} for x in q4_ids],
        "Q5": [{"params": {}, "expected": o_q5(facts)}],
        "Q6": [{"params": {"entity": e}, "expected": o_q6(facts, e)} for e in q6_ents],
        "Q7a": [{"params": {"lead": l}, "expected": o_q7a(facts, l)} for l in lead_sample],
        "Q7b": [{"params": {"lead": l}, "expected": o_q7b(facts, l)} for l in lead_sample],
        "Q8": [{"params": {"lead": l, "terms": t}, "expected": o_q8(facts, l, t)} for l, t in q8],
    }
    truth["W1"] = build_writes(g, ds, rng)
    return truth


def build_writes(g: Gen, ds: dict, rng: random.Random) -> list[dict]:
    """36 independent upserts: 12 duplicates, 12 conflicting values, 12 brand-new keys."""
    facts = ds["facts"]
    groups: dict[tuple, list[dict]] = {}
    for f in facts:
        if o_active(f) and not f["attribute"].startswith(("event-", "summary-", "promoted-")):
            groups.setdefault((f["scope"], f["subject"], f["attribute"]), []).append(f)
    singles = sorted(k for k, v in groups.items() if len(v) == 1)
    picks = rng.sample(singles, 24)
    out = []
    n_ids = len(facts)
    for i, key in enumerate(picks):
        old = groups[key][0]
        n_ids += 1
        nf = {k: old[k] for k in ("scope", "type", "visibility", "subject", "attribute", "importance")}
        nf["id"] = f"f-{n_ids:06d}"
        nf["created_at"] = NOW_TS - 60
        nf["source_id"] = old["source_id"]
        nf["derived_from"] = []
        nf["supersedes"] = []
        nf["superseded_by"] = None
        nf["entities"] = list(old["entities"])
        if i < 12:
            nf["value"] = old["value"]
            nf["text"] = "Re-stated: " + old["text"]
            exp = {"action": "skip_duplicate", "ids": [old["id"]]}
        else:
            nf["value"] = old["value"] + "-changed"
            nf["text"] = "Changed: " + old["text"]
            exp = {"action": "supersede", "ids": [old["id"]]}
        nf["name"] = f"{g.entities[nf['subject']]['name']} {nf['attribute']}".replace("-", " ")
        nf["description"] = nf["text"][:90]
        out.append({"params": {"fact": nf}, "expected": exp})
    leads = [s["id"] for s in ds["scopes"] if s["kind"] == "lead"]
    for i in range(12):
        n_ids += 1
        lead = leads[i % len(leads)]
        subj = g.project_entity[lead]
        nf = {"id": f"f-{n_ids:06d}", "scope": lead, "type": "project", "visibility": "parent",
              "subject": subj, "attribute": f"brand-new-attr-{i}", "value": f"value-{i}",
              "text": f"{lead.split(':', 1)[1]}: brand new fact number {i}.", "entities": [subj],
              "importance": 3, "created_at": NOW_TS - 60, "source_id": facts[0]["source_id"],
              "derived_from": [], "supersedes": [], "superseded_by": None}
        nf["name"] = f"{g.entities[subj]['name']} brand new attr {i}"
        nf["description"] = nf["text"][:90]
        out.append({"params": {"fact": nf}, "expected": {"action": "insert", "ids": []}})
    return [out[j] for i in range(12) for j in (i, 12 + i, 24 + i)]


def self_check(g: Gen, ds: dict, truth: dict) -> list[str]:
    problems = []
    q5 = truth["Q5"][0]["expected"]
    if len(q5) != g.planted["contradictions_unresolved"]:
        problems.append(f"Q5 oracle found {len(q5)} unresolved groups, planted {g.planted['contradictions_unresolved']}")
    keys = {(a, b, c) for a, b, c, _ in q5}
    if keys != set(g.unresolved_keys):
        problems.append("Q5 keys differ from planted unresolved keys")
    ids = {f["id"] for f in ds["facts"]}
    for f in ds["facts"]:
        for x in f["derived_from"] + f["supersedes"]:
            if x not in ids:
                problems.append(f"dangling ref {x}")
        if f["created_at"] > NOW_TS or f["created_at"] < START_TS:
            problems.append(f"timestamp out of window {f['id']}")
    for q, inst in truth.items():
        if q in ("Q2", "Q5", "W1"):
            continue
        empty = sum(1 for i in inst if not i["expected"])
        if empty == len(inst):
            problems.append(f"{q}: every instance has an empty answer")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-facts", type=int, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    g = Gen(a.n_facts, a.seed)
    g.generate()
    ds = g.dataset()
    truth = build_truth(g, ds)
    probs = self_check(g, ds, truth)
    import os
    os.makedirs(a.out, exist_ok=True)
    dpath = os.path.join(a.out, f"dataset-{a.n_facts}.json")
    tpath = os.path.join(a.out, f"truth-{a.n_facts}.json")
    dbytes = json.dumps(ds, sort_keys=True, separators=(",", ":")).encode()
    tbytes = json.dumps(truth, sort_keys=True, separators=(",", ":")).encode()
    with open(dpath, "wb") as fh:
        fh.write(dbytes)
    with open(tpath, "wb") as fh:
        fh.write(tbytes)
    facts = ds["facts"]
    act = [f for f in facts if o_active(f)]
    stats = {
        "n_facts": len(facts), "n_active": len(act), "n_superseded": len(facts) - len(act),
        "n_scopes": len(ds["scopes"]), "n_leads": len(ds["scopes"]) - 1,
        "n_entities": len(ds["entities"]), "n_sources": len(ds["sources"]),
        "by_scope_parent": sum(1 for f in facts if f["scope"] == "parent"),
        "by_type": {t: sum(1 for f in facts if f["type"] == t) for t in sorted(HALF_LIFE_DAYS)},
        "by_visibility": {v: sum(1 for f in facts if (f["visibility"] if not f["visibility"].startswith("lead:") else "targeted") == v)
                          for v in ("global", "local", "parent", "targeted")},
        "source_kinds": {k: sum(1 for s in ds["sources"] if s["kind"] == k) for k in ("telegram", "session", "tool", "file")},
        "fact_source_kinds": {k: sum(1 for f in facts if f["source_id"].split("-")[0] == p)
                              for k, p in (("telegram", "tg"), ("session", "ses"), ("tool", "tool"), ("file", "file"))},
        "days_span": round((max(f["created_at"] for f in facts) - min(f["created_at"] for f in facts)) / DAY, 1),
        "entities_in_2plus_leads": len(truth["Q2"][0]["expected"]),
        "q5_unresolved_groups": len(truth["Q5"][0]["expected"]),
        "planted": g.planted,
        "result_sizes": {q: [len(i["expected"]) if not isinstance(i["expected"], dict) or q != "Q4" else len(i["expected"]["facts"]) + len(i["expected"]["sources"]) for i in inst][:10]
                         for q, inst in truth.items() if q not in ("Q2", "W1")},
        "dataset_sha256": hashlib.sha256(dbytes).hexdigest(),
        "truth_sha256": hashlib.sha256(tbytes).hexdigest(),
        "dataset_path": dpath, "truth_path": tpath,
        "self_check_problems": probs,
    }
    print(json.dumps(stats, indent=1, sort_keys=True))
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
