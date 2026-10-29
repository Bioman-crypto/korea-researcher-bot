"""주제 찾기 사다리 — 디스코드 화면과 버튼 처리.

버튼·메뉴는 DynamicItem(custom_id 'lad:b:<동작>' / 'lad:s:<동작>')으로 만들어 봇이 재시작돼도 옛 메시지의 버튼이 눌린다.
화면을 보낼 때는 View를 stop()해서 보관하지 않는다 — 보관하면 같은 클릭이 두 번 처리된다(AI 호출 비용 두 배).
"""
import asyncio
import io
import logging
import os

import discord

import config
from . import engine, llm, store, texts as T

logger = logging.getLogger(__name__)

LIMITS = {"extract": int(os.getenv("LADDER_LIMIT_EXTRACT", "3")),
          "coach": int(os.getenv("LADDER_LIMIT_COACH", "10")),
          "signal": int(os.getenv("LADDER_LIMIT_SIGNAL", "3"))}
COLOR = discord.Color.from_rgb(88, 101, 242)
_locks: dict = {}


# ---------- 공통 ----------
def is_open():
    return os.getenv("LADDER_OPEN", "false").lower() == "true"


def _roles(member):
    return {r.name.lower() for r in getattr(member, "roles", [])}


def is_admin(member):
    return config.ADMIN_ROLE.lower() in _roles(member)


def allowed(member):
    """시범 운영 중(LADDER_OPEN=false)엔 bot-admin만, 연 뒤엔 /search 와 같은 역할."""
    if is_admin(member):
        return True
    return is_open() and config.OPERATOR_ROLE.lower() in _roles(member)


def _lock(uid):
    return _locks.setdefault(uid, asyncio.Lock())


def _name(user):
    return getattr(user, "display_name", None) or str(user)


def _cut(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


def layout(*items):
    v = discord.ui.View(timeout=None)
    for it in items:
        v.add_item(it)
    v.stop()                       # 보관하지 않는다 (위 설명)
    return v


async def ephemeral(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def room_state(interaction):
    """방 안 버튼: 누른 사람이 이 방 주인일 때만 상태를 돌려준다."""
    st = store.get(interaction.user.id)
    if not st or st.get("thread_id") != interaction.channel_id:
        await ephemeral(interaction, T.NOT_YOUR_ROOM)
        return None
    return st


# ---------- 버튼·메뉴 ----------
class Btn(discord.ui.DynamicItem[discord.ui.Button], template=r"lad:b:(?P<act>[a-z_0-9]+)"):
    def __init__(self, act, label="…", style=discord.ButtonStyle.secondary, disabled=False, row=None):
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"lad:b:{act}", disabled=disabled), row=row)
        self.act = act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"])

    async def callback(self, interaction):
        h = BUTTONS.get(self.act)
        if h:
            await _guard(h, interaction)


class Sel(discord.ui.DynamicItem[discord.ui.Select], template=r"lad:s:(?P<act>[a-z_0-9]+)"):
    def __init__(self, act, options=None, placeholder=None, min_values=0, max_values=1, row=None):
        super().__init__(discord.ui.Select(custom_id=f"lad:s:{act}", options=options or [discord.SelectOption(label="-")],
                                           placeholder=placeholder, min_values=min_values, max_values=max_values), row=row)
        self.act = act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"])

    async def callback(self, interaction):
        h = SELECTS.get(self.act)
        if h:
            await _guard(h, interaction, list((interaction.data or {}).get("values") or []))


async def _guard(handler, interaction, *args):
    try:
        await handler(interaction, *args)
    except Exception:
        logger.exception("ladder handler %s failed", getattr(handler, "__name__", handler))
        try:
            await ephemeral(interaction, T.ERROR)
        except Exception:
            pass


def _error_keep(text):
    """AI 호출 실패 안내. 학생이 쓴 글이 있으면 다시 붙여 넣을 수 있게 같이 보여 준다 (실패한 건 한도에서 빠짐)."""
    return _cut(T.ERROR + (f"\n{T.ERROR_KEEP}\n{text}" if text else ""), 2000)


def _options(values, selected=(), desc=None):
    """메뉴 선택지 (최대 25개, 글자 100자). 같은 값은 한 번만."""
    out, seen = [], set()
    for v in values:
        key = _cut(v, 100)
        if key in seen:
            continue
        seen.add(key)
        out.append(discord.SelectOption(label=key, value=key, default=v in selected,
                                        description=_cut(desc(v), 100) if desc and desc(v) else None))
    return out[:25]


def _offered(interaction, custom_id):
    """그 메시지의 메뉴에 실제로 보였던 선택지 (메시지가 오래돼 지금 상태와 달라도 맞게)."""
    for row in (interaction.message.components if interaction.message else []):
        for c in getattr(row, "children", []):
            if getattr(c, "custom_id", None) == custom_id:
                return [o.value for o in c.options]
    return []


# ---------- 화면 ----------
def _guide_embed(title, body, color=COLOR):
    nums = {"extract": LIMITS["extract"], "coach": LIMITS["coach"], "signal": LIMITS["signal"]}
    return discord.Embed(title=title, description=_cut(body.format(**nums) if "{" in body else body, 4096), color=color)


def guide_embeds():
    """자세한 사용법 장들 + 맨 끝에 한 화면 요약."""
    return [_guide_embed(t, b) for t, b in T.GUIDE_PAGES] + [_guide_embed(*T.GUIDE_START, color=discord.Color.green())]


def guide_messages():
    """설명서를 메시지 단위로 나눈다. 요약 카드는 늘 따로 맨 마지막 메시지 — 채널을 열면 맨 아래가 먼저 보이고,
    방 만들기 버튼도 거기에 붙는다. (메시지 하나에 임베드 합계 6000자·10개까지)"""
    *pages, start = guide_embeds()
    out, cur, size = [], [], 0
    for e in pages:
        if cur and (size + len(e) > 5800 or len(cur) == 10):
            out.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += len(e)
    return out + ([cur] if cur else []) + [[start]]


def welcome_panel(name):
    qs = "\n".join(f"**{i + 1}.** {q}" for i, (q, _, _) in enumerate(T.INTERVIEW))
    content = T.WELCOME.format(name=name) + "\n\n" + T.INTERVIEW_QUESTIONS_INTRO + "\n" + qs
    return {"content": _cut(content, 2000), "view": layout(Btn("interview", T.BTN_INTERVIEW, discord.ButtonStyle.primary))}


def keywords_panel():
    return {"content": T.KEYWORDS_ASK,
            "view": layout(Btn("keywords", T.BTN_KEYWORDS, discord.ButtonStyle.primary), Btn("no_keywords", T.BTN_NO_KEYWORDS))}


def s1_panel(st):
    """1단계 결과. 학생이 '무엇을 눌러 무엇을 얻는지' 헷갈리지 않게 할 일을 번호로 적고,
    메뉴는 연구 키워드 추천 하나만 둔다 (경험 키워드는 이미 다 골라져 있어 고칠 때만 따로 연다)."""
    probs = [w["word"] for w in st["words"] if w.get("kind") != "강점"]
    strengths = [w["word"] for w in st["words"] if w.get("kind") == "강점"]
    lines = []
    if st["echo"]:
        lines += [f"**{T.S1_ECHO}**", *[f"• {T.plain(x)}" for x in st["echo"]], ""]
    if probs or strengths:
        lines.append(f"**{T.S1_WORDS_HEAD}**")
        if probs:
            lines.append(f"• {T.S1_WORDS_LIST} " + " · ".join(probs))
        if strengths:
            lines.append(f"• {T.S1_STRENGTH_LIST} " + " · ".join(strengths))
        lines += [T.S1_EDIT_NOTE, ""]
    sug = engine.suggest_options(st)
    todo, n = [], 1
    if st["follow_up"]:
        todo.append((T.S1_TODO_FOLLOW_THIN if st["thin"] else T.S1_TODO_FOLLOW).format(n=n, q=T.plain(st["follow_up"])))
        n += 1
    if sug:
        todo.append(T.S1_TODO_SUGGEST.format(n=n))
        n += 1
    elif not st["kws"]:
        todo.append(T.S1_TODO_NO_KWS.format(n=n))
        n += 1
    todo.append(T.S1_TODO_NEXT.format(n=n))
    lines += [f"**{T.S1_TODO_HEAD}**", "\n\n".join(todo), "", T.S1_WHAT_NEXT]
    e = discord.Embed(title=T.S1_TITLE, description=_cut("\n".join(lines), 4096), color=COLOR)
    items = []
    desc = dict(sug)
    for i, chunk in enumerate((sug[:25], sug[25:50])):
        opts = _options([t for t, _ in chunk], set(st["kws"]), desc.get)
        if opts:                                   # 최대 선택 수 = 실제 선택지 수 (넘으면 디스코드가 거부)
            items.append(Sel("suggest" + ("" if i == 0 else "2"), opts, T.S1_SUGGEST_PH if i == 0 else T.S1_SUGGEST2_PH,
                             0, len(opts), row=i))
    if st["follow_up"]:
        items.append(Btn("follow_up", T.BTN_FOLLOW_UP, discord.ButtonStyle.primary if st["thin"] else discord.ButtonStyle.secondary, row=2))
    items.append(Btn("to_s2", T.BTN_TO_S2, discord.ButtonStyle.secondary if st["thin"] else discord.ButtonStyle.primary, row=2))
    items += [Btn("edit_words", T.BTN_EDIT_WORDS, row=3), Btn("edit_answers", T.BTN_EDIT_ANSWERS, row=3),
              Btn("edit_keywords", T.BTN_EDIT_KEYWORDS, row=3)]
    return {"embed": e, "view": layout(*items)}


def words_panel(st):
    """경험 키워드 고치기 (나만 보이는 메시지)."""
    probs = [w["word"] for w in st["words"] if w.get("kind") != "강점"]
    strengths = [w["word"] for w in st["words"] if w.get("kind") == "강점"]
    on = set(engine.chosen_words(st))
    quote = {w["word"]: w.get("quote") for w in st["words"]}
    why = lambda w: quote.get(w) and "근거: " + quote[w]
    items = []
    for act, pool, ph, row in (("words", probs, T.S1_WORDS_PH, 0), ("strengths", strengths, T.S1_STRENGTH_PH, 1)):
        opts = _options(pool, on, why)
        if opts:
            items.append(Sel(act, opts, ph, 0, len(opts), row=row))
    items.append(Btn("add_word", T.BTN_ADD_WORD, row=2))
    return {"content": T.EDIT_WORDS_MSG, "view": layout(*items)}


def s2_panel(st, note=""):
    ws, ks = engine.pair_words(st), st["kws"]
    body = [note] if note else []
    body.append(T.S2_BODY)
    if not ws:
        body.append("⚠️ " + T.S2_NEED_WORDS)
    if not ks:
        body.append("⚠️ " + T.S2_NEED_KWS)
    e = discord.Embed(title=T.S2_TITLE, description="\n\n".join(body), color=COLOR)
    cur = st.get("cur") or {}
    items = []
    if ws:
        items.append(Sel("pair_word", _options(ws, {cur.get("word")}), T.S2_WORD_PH, 1, 1, row=0))
    if ks:
        items.append(Sel("pair_kw", _options(ks, {cur.get("kw")}), T.S2_KW_PH, 1, 1, row=1))
    ok = bool(ws and ks)
    items += [Btn("write", T.BTN_WRITE, discord.ButtonStyle.primary, disabled=not ok, row=2),
              Btn("random", T.BTN_RANDOM, disabled=not ok, row=2), Btn("edit_keywords", T.BTN_EDIT_KEYWORDS, row=2)]
    n = len(engine.signal_hist(st))
    items += [Btn("signal", T.BTN_SIGNAL, discord.ButtonStyle.success, row=3), Btn("record", T.BTN_RECORD, row=3),
              Btn("reset", T.BTN_RESET, discord.ButtonStyle.danger, row=3)]
    if n < 3:
        e.set_footer(text=T.SIGNAL_GATE.format(n=n))
    return {"embed": e, "view": layout(*items)}


def feedback_panel(h):
    lv = h["level"]
    n = lv if lv in T.LEVELS else 0
    bar = "🟩" * n + "⬜" * (6 - n)                              # 6칸 중 몇 칸인지 한눈에 (단계라는 게 보이게)
    num = "①②③④⑤⑥"
    steps = " → ".join(f"**{num[i - 1]} {T.LEVELS[i]}**" if i == n else f"{num[i - 1]} {T.LEVELS[i]}" for i in range(1, 7))
    meaning = f"\n➡️ **{n}단계 {T.LEVELS[n]}** = {T.LEVEL_MEANING.get(n, '')}" if n else ""
    e = discord.Embed(title=T.FB_TITLE.format(n=n or "?", level=T.LEVELS.get(n, "")),
                      description=_cut(f"> {h['q']}\n\n{bar}\n{steps}{meaning}\n{T.FB_LADDER_NOTE}", 4096), color=COLOR)
    if h["reason"]:
        e.add_field(name=T.FB_REASON, value=_cut(T.plain(h["reason"]), 1024), inline=False)
    if h.get("bg"):
        e.add_field(name=T.FB_BG, value=_cut("\n".join(f"• {T.plain(x)}" for x in h["bg"]["lines"]) + "\n" + T.FB_BG_NOTE, 1024), inline=False)
    if h["hint"]:
        e.add_field(name=T.FB_HINT, value=_cut(T.plain(h["hint"]), 1024), inline=False)
    if h["next"]:
        e.add_field(name=T.FB_NEXT, value=_cut(T.plain(h["next"]), 1024), inline=False)
    check = [T.FB_BOTH_OK if h["uses_both"] else T.FB_BOTH_NO.format(word=h["word"], kw=h["kw"]),
             T.FB_LINK_OK if h["my_link"] else T.FB_LINK_NO]
    if h.get("fact_note"):
        check.append(T.FB_FACT.format(note=T.plain(h["fact_note"])))
    if not (h["uses_both"] and h["my_link"]):
        check.append(T.FB_CHECK_TAIL)
    e.add_field(name=T.FB_CHECK, value=_cut("\n".join(check), 1024), inline=False)
    e.set_footer(text=_cut(f"{h['word']} × {h['kw']}", 2048))
    warn = T.FB_COPIED_HINT if h.get("copied", 0) >= 40 else T.FB_COPIED_BG if h.get("bgCopied", 0) >= 40 else None
    view = layout(Btn("rewrite", T.BTN_REWRITE, discord.ButtonStyle.primary), Btn("other_pair", T.BTN_OTHER_PAIR),
                  Btn("signal", T.BTN_SIGNAL, discord.ButtonStyle.success), Btn("record", T.BTN_RECORD))
    return {"content": warn, "embed": e, "view": view}


def signal_embeds(s):
    out = [discord.Embed(title=T.S3_TITLE, description=T.S3_THIN if s.get("thin") else None, color=discord.Color.green())]
    for c in s.get("candidates") or []:
        e = discord.Embed(title=_cut(T.plain(c.get("title")), 256), description=_cut(T.plain(c.get("why")), 4096), color=discord.Color.green())
        ev = c.get("evidence") or []
        if ev:
            e.add_field(name=T.S3_EVIDENCE, value=_cut("\n".join(f"• {T.plain(x)}" for x in ev), 1024), inline=False)
        if c.get("explore"):
            e.add_field(name=T.S3_EXPLORE, value=_cut(T.plain(c["explore"]), 1024), inline=False)
        cmd, extra = engine.search_cmd(c.get("search_terms"))
        if cmd:
            note = T.S3_SEARCH_NOTE + (T.S3_SEARCH_EXTRA.format(extra=", ".join(f"'{x}'" for x in extra)) if extra else "")
            e.add_field(name=T.S3_SEARCH, value=_cut(f"```\n{cmd}\n```\n{note}", 1024), inline=False)
        out.append(e)
    d = discord.Embed(title=T.S3_DRAFT, description=_cut(f"```\n{T.plain(s.get('problem_draft'))}\n```\n{T.S3_DRAFT_NOTE}", 4096),
                      color=discord.Color.green())
    if s.get("keywords"):
        d.add_field(name=T.S3_KEYWORDS, value=_cut(", ".join(map(str, s["keywords"])), 1024), inline=False)
    if s.get("next_step"):
        d.add_field(name=T.S3_NEXT, value=_cut(T.plain(s["next_step"]), 1024), inline=False)
    d.set_footer(text=T.S3_AGAIN)
    out.append(d)
    return out


async def send_embeds(interaction, embeds, view):
    """디스코드는 메시지 하나에 임베드 글자 합 6000자까지 — 넘치면 나눠 보낸다. view는 마지막 메시지에."""
    batch, size = [], 0
    for i, e in enumerate(embeds):
        n = len(e)
        if batch and (size + n > 5800 or len(batch) == 10):
            await interaction.followup.send(embeds=batch)
            batch, size = [], 0
        batch.append(e)
        size += n
    await interaction.followup.send(embeds=batch, view=view)


def resume_panel(st, name):
    if not any((a or "").strip() for a in st["answers"]):
        return welcome_panel(name)
    if not st["words"]:
        return keywords_panel()
    return s2_panel(st)


# ---------- 입력창 ----------
class InterviewModal(discord.ui.Modal):
    def __init__(self, st):
        super().__init__(title=T.MODAL_INTERVIEW_TITLE, timeout=3600)
        self.inputs = []
        for (_, label, ph), a in zip(T.INTERVIEW, st["answers"]):
            ti = discord.ui.TextInput(label=label, placeholder=ph, default=a or None, required=False,
                                      style=discord.TextStyle.paragraph, max_length=1000)
            self.inputs.append(ti)
            self.add_item(ti)

    async def on_submit(self, interaction):
        await _guard(on_interview, interaction, [ti.value.strip() for ti in self.inputs])


class KeywordsModal(discord.ui.Modal):
    def __init__(self, st):
        super().__init__(title=T.MODAL_KEYWORDS_TITLE, timeout=3600)
        self.kw = discord.ui.TextInput(label=T.KEYWORDS_LABEL, placeholder=T.KEYWORDS_PLACEHOLDER,
                                       default=", ".join(st["kws"]) or None, required=False,
                                       style=discord.TextStyle.paragraph, max_length=1000)
        self.add_item(self.kw)

    async def on_submit(self, interaction):
        await _guard(on_keywords, interaction, self.kw.value)


class AddWordModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=T.MODAL_ADD_WORD_TITLE, timeout=1800)
        self.word = discord.ui.TextInput(label=T.ADD_WORD_LABEL, placeholder=T.ADD_WORD_PLACEHOLDER, max_length=60)
        self.add_item(self.word)

    async def on_submit(self, interaction):
        await _guard(on_add_word, interaction, self.word.value.strip())


class FollowUpModal(discord.ui.Modal):
    def __init__(self, q):
        super().__init__(title=T.MODAL_FOLLOW_TITLE, timeout=3600)
        self.a = discord.ui.TextInput(label=T.FOLLOW_LABEL, placeholder=_cut(q, 100),
                                      style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.a)

    async def on_submit(self, interaction):
        await _guard(on_follow_up, interaction, self.a.value.strip())


class QuestionModal(discord.ui.Modal):
    def __init__(self, word, kw, default="", rewrite=False):
        super().__init__(title=_cut(f"{word} × {kw}", 45), timeout=3600)
        self.word, self.kw = word, kw
        self.q = discord.ui.TextInput(label=T.REWRITE_LABEL if rewrite else T.QUESTION_LABEL,
                                      placeholder=T.QUESTION_PLACEHOLDER, default=default or None,
                                      style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.q)

    async def on_submit(self, interaction):
        await _guard(on_question, interaction, self.word, self.kw, self.q.value.strip())


# ---------- 방 열기 ----------
async def open_room(interaction):
    if interaction.guild is None:
        await ephemeral(interaction, T.NO_PERMISSION)
        return
    if not allowed(interaction.user):
        await ephemeral(interaction, T.NOT_OPEN if config.OPERATOR_ROLE.lower() in _roles(interaction.user) else T.NO_PERMISSION)
        return
    uid, name = interaction.user.id, _name(interaction.user)
    st = store.get(uid)
    if st and st.get("thread_id"):
        th = interaction.guild.get_thread(st["thread_id"])
        if th is None:
            try:
                th = await interaction.guild.fetch_channel(st["thread_id"])
            except (discord.NotFound, discord.Forbidden):
                th = None
        if th is not None:
            if interaction.channel_id == th.id:               # 내 방 안에서 다시 부르면 지금 단계 화면을 다시 보여 준다
                await interaction.response.send_message(**resume_panel(st, name))
            else:
                await ephemeral(interaction, T.ROOM_EXISTS.format(mention=th.mention))
            return
    await interaction.response.defer(ephemeral=True, thinking=True)
    parent = interaction.channel.parent if isinstance(interaction.channel, discord.Thread) else interaction.channel
    try:
        th = await parent.create_thread(name=_cut(T.THREAD_NAME.format(name=name), 100), type=discord.ChannelType.private_thread,
                                        invitable=False, auto_archive_duration=10080)
        await th.add_user(interaction.user)
    except (discord.Forbidden, discord.HTTPException, AttributeError) as exc:
        logger.error("ladder: thread create failed: %s", exc)
        await interaction.followup.send(T.ROOM_FAILED, ephemeral=True)
        return
    st = st or engine.new_state()
    st["thread_id"] = th.id                                    # 방이 지워졌다면 기록은 그대로 두고 새 방으로 옮긴다
    await store.save(uid, name, st)
    await th.send(**welcome_panel(name))
    if any((a or "").strip() for a in st["answers"]):
        await th.send(**resume_panel(st, name))
    store.log(uid, name, "room")
    await interaction.followup.send(T.ROOM_CREATED.format(mention=th.mention), ephemeral=True)


# ---------- 1단계 ----------
async def b_interview(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_modal(InterviewModal(st))


async def on_interview(interaction, answers):
    st = await room_state(interaction)
    if not st:
        return
    if not any(answers):
        await ephemeral(interaction, T.NEED_ANSWER)
        return
    st["answers"] = answers
    if not st["words"] and not st.get("kw_asked"):              # 처음이면 연구 키워드부터 묻는다
        st["kw_asked"] = True
        await interaction.response.send_message(**keywords_panel())
        await store.save(interaction.user.id, _name(interaction.user), st)
        return
    await run_extract(interaction, st)


async def b_keywords(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_modal(KeywordsModal(st))


async def on_keywords(interaction, text):
    st = await room_state(interaction)
    if not st:
        return
    st["kws"] = engine.parse_kws(text)
    if not st["words"]:
        await run_extract(interaction, st)
        return
    await interaction.response.send_message(**s2_panel(st, T.SAVED))
    await store.save(interaction.user.id, _name(interaction.user), st)


async def b_no_keywords(interaction):
    st = await room_state(interaction)
    if st:
        await run_extract(interaction, st)


async def run_extract(interaction, st, keep=""):
    uid, name = interaction.user.id, _name(interaction.user)
    lock = _lock(uid)
    if lock.locked():
        await ephemeral(interaction, T.BUSY)
        return False
    async with lock:
        if not engine.bump_usage(st, "extract", LIMITS["extract"]):
            await ephemeral(interaction, T.DAILY_LIMIT)
            return False
        await interaction.response.defer(thinking=True)
        try:
            r, meta = await llm.json_call("deep", engine.extract_prompt(st), engine.DEEP_SYSTEM, check=lambda o: engine.apply_extract(engine.new_state(), o))
        except llm.LLMError as exc:
            st["usage"]["extract"] -= 1                         # 실패한 건 하루 한도에서 빼 준다
            store.log(uid, name, "extract", error=str(exc))
            await interaction.followup.send(_error_keep(keep))
            return False
        engine.apply_extract(st, r)
        await store.save(uid, name, st)
        store.log(uid, name, "extract", deep=meta, detail=r)
        await interaction.followup.send(**s1_panel(st))
        return True


async def s_words(interaction, values, kind="문제"):
    st = await room_state(interaction)
    if not st:
        return
    shown = set(_offered(interaction, "lad:s:" + ("words" if kind == "문제" else "strengths")))
    for w in st["words"]:
        key = _cut(w["word"], 100)
        if key in shown:
            w["on"] = key in values
    await interaction.response.defer()                 # 디스코드 3초 기한 — 저장은 응답 뒤에
    await store.save(interaction.user.id, _name(interaction.user), st)


async def s_strengths(interaction, values):
    await s_words(interaction, values, "강점")


async def s_suggest(interaction, values, cid="lad:s:suggest"):
    st = await room_state(interaction)
    if not st:
        return
    engine.set_suggested(st, _offered(interaction, cid), values)
    msg = T.SUGGEST_SAVED.format(kws=", ".join(st["kws"])) if st["kws"] else T.SUGGEST_EMPTY
    await interaction.response.send_message(msg, ephemeral=True)       # 고른 게 어디로 갔는지 바로 보여 준다
    await store.save(interaction.user.id, _name(interaction.user), st)


async def s_suggest2(interaction, values):
    await s_suggest(interaction, values, "lad:s:suggest2")


async def b_follow_up(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_modal(FollowUpModal(T.plain(st.get("follow_up") or "")))


async def on_follow_up(interaction, a):
    st = await room_state(interaction)
    if not st or not a:
        return
    q = st.get("follow_up") or ""
    i = engine.follow_target(st["answers"], q)                     # 추가 질문이 가리킨 답 칸 뒤에 덧붙인다
    before = st["answers"][i]
    st["answers"][i] = "\n".join(x for x in ((before or "").strip(), T.FOLLOW_TAG.format(q=q, a=a)) if x)
    if not await run_extract(interaction, st, keep=a):
        st["answers"][i] = before                                  # 실패하면 되돌린다 (다시 눌러도 두 번 붙지 않게)


async def b_edit_words(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_message(**words_panel(st), ephemeral=True)


async def b_add_word(interaction):
    if await room_state(interaction):
        await interaction.response.send_modal(AddWordModal())


async def on_add_word(interaction, word):
    st = await room_state(interaction)
    if not st or not word:
        return
    if not any(w["word"] == word for w in st["words"]):
        st["words"].append({"word": word, "kind": "문제", "from": 0, "quote": "", "on": True})
    await interaction.response.send_message(T.WORD_ADDED.format(word=word), ephemeral=True)
    await store.save(interaction.user.id, _name(interaction.user), st)


async def b_to_s2(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_message(**s2_panel(st))


# ---------- 2단계 ----------
async def s_pair(interaction, values, field):
    st = await room_state(interaction)
    if not st:
        return
    pool = engine.pair_words(st) if field == "word" else st["kws"]
    v = values[0] if values else ""
    st.setdefault("cur", {})[field] = next((x for x in pool if _cut(x, 100) == v), v)
    await interaction.response.defer()
    await store.save(interaction.user.id, _name(interaction.user), st)    # 재시작 뒤에도 고른 조합이 남도록


async def s_pair_word(interaction, values):
    await s_pair(interaction, values, "word")


async def s_pair_kw(interaction, values):
    await s_pair(interaction, values, "kw")


async def b_write(interaction):
    st = await room_state(interaction)
    if not st:
        return
    cur = st.get("cur") or {}
    if cur.get("word") not in engine.pair_words(st) or cur.get("kw") not in st["kws"]:
        await ephemeral(interaction, T.PICK_FIRST)
        return
    await interaction.response.send_modal(QuestionModal(cur["word"], cur["kw"]))


async def b_random(interaction):
    st = await room_state(interaction)
    if not st:
        return
    pair = engine.random_pair(st)
    if not pair:
        await ephemeral(interaction, T.PICK_FIRST)
        return
    st["cur"] = {"word": pair[0], "kw": pair[1]}
    await interaction.response.send_modal(QuestionModal(*pair))


async def b_rewrite(interaction):
    st = await room_state(interaction)
    if not st:
        return
    e = (st.get("fb") or {}).get(str(interaction.message.id)) or (st["hist"][-1] if st["hist"] else None)
    if not e:
        await interaction.response.send_message(**s2_panel(st))
        return
    st["cur"] = {"word": e["word"], "kw": e["kw"]}
    await interaction.response.send_modal(QuestionModal(e["word"], e["kw"], e["q"], rewrite=True))


async def on_question(interaction, word, kw, q):
    st = await room_state(interaction)
    if not st or not q:
        return
    uid, name = interaction.user.id, _name(interaction.user)
    lock = _lock(uid)
    if lock.locked():
        await ephemeral(interaction, T.BUSY)
        return
    async with lock:
        if not engine.bump_usage(st, "coach", LIMITS["coach"]):
            await ephemeral(interaction, T.DAILY_LIMIT)
            return
        await interaction.response.defer(thinking=True)
        # 판정(검증한 프롬프트)과 배경지식(별도 프롬프트)을 동시에. 배경이 실패해도 판정은 보여 준다.
        jr, br = await asyncio.gather(
            llm.json_call("judge", engine.judge_prompt(word, kw, q), engine.JUDGE_SYSTEM),
            llm.json_call("deep", engine.bg_prompt(word, kw, q), engine.DEEP_SYSTEM), return_exceptions=True)
        if isinstance(jr, Exception):
            st["usage"]["coach"] -= 1
            store.log(uid, name, "coach", word=word, kw=kw, q=q, error=str(jr))
            await interaction.followup.send(_error_keep(q))
            return
        (judged, jmeta), (bg, bmeta) = jr, (br if not isinstance(br, Exception) else (None, {}))
        h, same = engine.apply_coach(st, word, kw, q, judged, bg)
        msg = await interaction.followup.send(**feedback_panel(h), wait=True)
        engine.remember_feedback(st, msg.id, h)
        await store.save(uid, name, st)
        store.log(uid, name, "coach", word=word, kw=kw, q=q, h=h, same=same, judge=jmeta, deep=bmeta,
                  error="" if not isinstance(br, Exception) else "bg: " + str(br),
                  detail={k: h[k] for k in ("reason", "hint", "next", "fact_note", "bg")})


async def b_other_pair(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_message(**s2_panel(st))


# ---------- 3단계 ----------
async def b_signal(interaction):
    st = await room_state(interaction)
    if not st:
        return
    n = len(engine.signal_hist(st))
    if n < 3:
        await ephemeral(interaction, T.SIGNAL_GATE.format(n=n))
        return
    uid, name = interaction.user.id, _name(interaction.user)
    lock = _lock(uid)
    if lock.locked():
        await ephemeral(interaction, T.BUSY)
        return
    async with lock:
        if not engine.bump_usage(st, "signal", LIMITS["signal"]):
            await ephemeral(interaction, T.DAILY_LIMIT)
            return
        await interaction.response.defer(thinking=True)
        try:
            r, meta = await llm.json_call("deep", engine.signal_prompt(st), engine.DEEP_SYSTEM,
                                          check=lambda o: engine.apply_signal(engine.new_state(), o))
        except llm.LLMError as exc:
            st["usage"]["signal"] -= 1
            store.log(uid, name, "signal", error=str(exc))
            await interaction.followup.send(T.ERROR)
            return
        engine.apply_signal(st, r)
        await store.save(uid, name, st)
        store.log(uid, name, "signal", deep=meta, detail=r)
        await send_embeds(interaction, signal_embeds(r), layout(Btn("other_pair", T.BTN_OTHER_PAIR), Btn("record", T.BTN_RECORD)))


# ---------- 기록 · 다시 ----------
def record_file(st, for_admin=False):
    data = engine.record_text(st, for_admin).encode("utf-8")
    return discord.File(io.BytesIO(data), filename=f"ladder_record_{engine.today_kst().replace('-', '')}.txt")


async def b_record(interaction):
    st = await room_state(interaction)
    if st:
        await interaction.response.send_message(T.RECORD_MSG, file=record_file(st))


async def b_reset(interaction):
    if await room_state(interaction):
        await interaction.response.send_message(T.RESET_CONFIRM, ephemeral=True,
                                                view=layout(Btn("reset_yes", T.BTN_RESET_YES, discord.ButtonStyle.danger)))


async def b_reset_yes(interaction):
    st = await room_state(interaction)
    if not st:
        return
    uid, name = interaction.user.id, _name(interaction.user)
    fresh = engine.new_state(st["thread_id"])
    fresh["usage"] = st.get("usage", {})                          # 하루 한도는 다시 시작해도 이어서 센다
    store.put(uid, fresh)
    await interaction.response.edit_message(content=T.RESET_DONE, view=None)
    store.log(uid, name, "reset", detail={"before": st})          # 지운 기록도 운영진 시트에는 남긴다
    await interaction.channel.send(**welcome_panel(name))
    await store.save(uid, name, fresh)


async def b_start_room(interaction):
    await open_room(interaction)


BUTTONS = {"interview": b_interview, "edit_answers": b_interview, "keywords": b_keywords, "edit_keywords": b_keywords,
           "no_keywords": b_no_keywords, "add_word": b_add_word, "to_s2": b_to_s2, "write": b_write, "random": b_random,
           "rewrite": b_rewrite, "other_pair": b_other_pair, "signal": b_signal, "record": b_record, "reset": b_reset,
           "reset_yes": b_reset_yes, "start_room": b_start_room, "follow_up": b_follow_up, "edit_words": b_edit_words}
SELECTS = {"words": s_words, "strengths": s_strengths, "suggest": s_suggest, "suggest2": s_suggest2,
           "pair_word": s_pair_word, "pair_kw": s_pair_kw}
