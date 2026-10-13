#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Who is the Undercover —— 搭在 party-engine 上的最小示例游戏（核 A 退化版）。

无阵营、无夜间，只有「发词 → 轮流描述 → 投票 → 揭晓」循环。
本文件只写核（约 120 行），壳全部由根目录 engine.py 提供。
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from engine import Game, Engine, add_log, find_player

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8092

ROLE_CIVILIAN = "civilian"
ROLE_SPY = "spy"
ROLE_NAMES = {ROLE_CIVILIAN: "平民", ROLE_SPY: "卧底"}

# 相似词对：平民词 / 卧底词
WORD_PAIRS = [
    ("薯条", "番茄酱"),
    ("大象", "长颈鹿"),
    ("微信", "QQ"),
    ("火锅", "麻辣烫"),
    ("咖啡", "奶茶"),
    ("篮球", "排球"),
    ("饺子", "包子"),
    ("猫", "狗"),
    ("苹果", "梨"),
    ("电影院", "KTV"),
]


def in_game(room):
    """在场且未淘汰的玩家。"""
    return [p for p in room["players"] if not p.get("left") and not p.get("out")]


class Undercover(Game):
    name = "谁是卧底"
    min_players = 4
    max_players = 10
    port = PORT

    def config_for(self, player_count):
        return {"spy": 1, "civilian": player_count - 1}

    def new_room_state(self, room):
        room.update({
            "words": None, "spy_pid": None, "round": 1,
            "order": [], "desc_index": 0, "descs": {},
            "votes": {}, "revealed": [], "last_reveal": None,
            "result_until": None,
        })

    def setup(self, room):
        room["words"] = dict(zip(("civilian", "spy"), random.choice(WORD_PAIRS)))
        roles = [ROLE_SPY] + [ROLE_CIVILIAN] * (room["player_count"] - 1)
        self.deal(room, roles)
        for p in room["players"]:
            p["out"] = False
        room["spy_pid"] = next(p["id"] for p in room["players"] if p["role"] == ROLE_SPY)
        room["revealed"] = []
        room["round"] = 1
        add_log(room, "游戏开始，请查看你的词并轮流描述")
        self.start_round(room)

    def start_round(self, room):
        room["phase"] = "describe"
        room["descs"] = {}
        room["votes"] = {}
        room["last_reveal"] = None
        alive = sorted(in_game(room), key=lambda x: x["seat"])
        room["order"] = [p["id"] for p in alive]
        room["desc_index"] = 0

    def word_for(self, room, p):
        return room["words"][p["role"]]

    # ---- 动作 ----
    def on_action(self, room, pid, action, body):
        if action == "describe":
            self.do_describe(room, pid, body)
        elif action == "vote":
            self.do_vote(room, pid, body)

    def do_describe(self, room, pid, body):
        if room["phase"] != "describe":
            return
        if pid != room["order"][room["desc_index"]]:
            return
        text = (body.get("text") or "").strip()[:30]
        if not text:
            return
        p = find_player(room, pid)
        room["descs"][pid] = text
        add_log(room, "%s 描述完毕" % p["name"])
        room["desc_index"] += 1
        if room["desc_index"] >= len(room["order"]):
            room["phase"] = "vote"
            add_log(room, "本轮描述结束，开始投票")

    def do_vote(self, room, pid, body):
        if room["phase"] != "vote":
            return
        p = find_player(room, pid)
        if not p or p.get("out") or p.get("left"):
            return
        target = body.get("target")
        tp = find_player(room, target) if target else None
        if not tp or tp.get("out") or tp.get("left") or tp["id"] == pid:
            return
        room["votes"][pid] = target
        if len(room["votes"]) >= len(in_game(room)):
            self.resolve_vote(room)

    def resolve_vote(self, room):
        tally = {}
        for t in room["votes"].values():
            tally[t] = tally.get(t, 0) + 1
        top = max(tally.values())
        leaders = [pid for pid, c in tally.items() if c == top]
        if len(leaders) != 1:
            add_log(room, "平票，无人淘汰，重新描述")
            room["round"] += 1
            self.start_round(room)
            return
        target = find_player(room, leaders[0])
        target["out"] = True
        is_spy = target["role"] == ROLE_SPY
        room["last_reveal"] = {
            "name": target["name"], "role": target["role"],
            "is_spy": is_spy, "word": room["words"][target["role"]],
        }
        room["revealed"].append(target["name"])
        if is_spy:
            self.winner(room, "civilian")
            add_log(room, "%s 是卧底，被投出！平民获胜！" % target["name"])
            return
        add_log(room, "%s 被投出，是平民（词：%s）" % (target["name"], room["words"]["civilian"]))
        civilians = [p for p in in_game(room) if p["role"] == ROLE_CIVILIAN]
        if len(civilians) <= 1:
            self.winner(room, "spy")
            add_log(room, "平民所剩无几，卧底获胜！")
            return
        room["phase"] = "reveal"
        room["result_until"] = time.time() + 5

    # ---- 时间推进 ----
    def tick(self, room):
        if room["phase"] == "reveal" and room.get("result_until") and time.time() >= room["result_until"]:
            room["round"] += 1
            self.start_round(room)

    def force(self, room):
        if room["phase"] == "describe":
            room["desc_index"] = len(room["order"])
            room["phase"] = "vote"
            add_log(room, "房主跳过剩余描述")
        elif room["phase"] == "vote":
            for p in in_game(room):
                room["votes"].setdefault(p["id"], None)
            self.resolve_vote(room)
        elif room["phase"] == "reveal":
            room["result_until"] = time.time()

    def reset(self, room):
        room.update({
            "words": None, "spy_pid": None, "round": 1,
            "order": [], "desc_index": 0, "descs": {},
            "votes": {}, "revealed": [], "last_reveal": None,
            "result_until": None,
        })

    def can_chat(self, room, p):
        return not p.get("out")

    # ---- 视图 ----
    def phase_view(self, room, p, view):
        view["round"] = room["round"]
        if room["phase"] == "describe":
            view["my_word"] = self.word_for(room, p)
            view["desc_order"] = [find_player(room, x)["name"] for x in room["order"]]
            view["desc_index"] = room["desc_index"]
            view["descs"] = [{"name": find_player(room, x)["name"], "text": room["descs"][x]}
                             for x in room["order"] if x in room["descs"]]
            if room["desc_index"] < len(room["order"]):
                view["current_desc_name"] = find_player(room, room["order"][room["desc_index"]])["name"]
            view["my_turn"] = room["desc_index"] < len(room["order"]) and room["order"][room["desc_index"]] == p["id"]
        elif room["phase"] == "vote":
            view["descs"] = [{"name": find_player(room, x)["name"], "text": room["descs"][x]}
                             for x in room["order"] if x in room["descs"]]
            view["my_vote"] = room["votes"].get(p["id"])
            view["voted"] = p["id"] in room["votes"]
            view["vote_count"] = len(room["votes"])
            view["vote_total"] = len(in_game(room))
        elif room["phase"] == "reveal":
            view["descs"] = [{"name": find_player(room, x)["name"], "text": room["descs"][x]}
                             for x in room["order"] if x in room["descs"]]
            view["last_reveal"] = room["last_reveal"]
        elif room["phase"] == "over":
            view["words"] = room["words"]
            view["spy_name"] = find_player(room, room["spy_pid"])["name"] if find_player(room, room["spy_pid"]) else "?"


if __name__ == "__main__":
    Engine(Undercover(), BASE_DIR).run()
