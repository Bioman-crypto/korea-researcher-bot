"""주제 찾기 사다리 (관심찾기 에이전트/ask-smile-daepcon 의 디스코드판).

bot.py 에서 setup(bot) 으로 명령어를 붙이고, setup_hook 에서 on_setup(bot) 으로 학생 상태를 불러온다.
여기서 무엇이 실패해도 /search 는 그대로 동작해야 한다 — bot.py 쪽에서 try로 감싼다.
"""
import logging

import discord
from discord import app_commands

from . import engine, llm, store, texts as T, ui

logger = logging.getLogger(__name__)


def setup(bot):
    bot.add_dynamic_items(ui.Btn, ui.Sel)

    @bot.tree.command(name="주제찾기사다리", description="내 주제 찾기 사다리 방을 열어요 (1강까지 들었다면 쓸 수 있어요)")
    async def open_cmd(interaction: discord.Interaction):
        await ui._guard(ui.open_room, interaction)

    @bot.tree.command(name="사다리도움말", description="주제 찾기 사다리 사용 설명서")
    async def help_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(embed=ui.guide_embed(), ephemeral=True)

    @bot.tree.command(name="사다리안내글", description="(운영진) 이 채널에 설명서와 '방 만들기' 버튼을 올려요")
    @app_commands.default_permissions(manage_guild=True)
    async def post_guide(interaction: discord.Interaction):
        if not ui.is_admin(interaction.user):
            await interaction.response.send_message(T.NO_PERMISSION, ephemeral=True)
            return
        await interaction.channel.send(embed=ui.guide_embed(),
                                       view=ui.layout(ui.Btn("start_room", T.GUIDE_START_BUTTON, discord.ButtonStyle.primary)))
        await interaction.response.send_message("올렸어요. 메시지 오른쪽 ⋯ → **메시지 고정**을 눌러 고정해 두세요.", ephemeral=True)

    @bot.tree.command(name="사다리기록", description="(운영진) 학생 한 명의 주제 찾기 사다리 기록 파일 받기")
    @app_commands.describe(student="기록을 볼 학생")
    @app_commands.default_permissions(manage_guild=True)
    async def admin_record(interaction: discord.Interaction, student: discord.Member):
        if not ui.is_admin(interaction.user):
            await interaction.response.send_message(T.NO_PERMISSION, ephemeral=True)
            return
        st = store.get(student.id)
        if not st:
            await interaction.response.send_message(f"{student.display_name}님은 아직 주제 찾기 사다리를 쓰지 않았어요.", ephemeral=True)
            return
        await interaction.response.send_message(f"{student.display_name}님 기록 (배경 설명 점검 숫자 포함)",
                                                file=ui.record_file(st, for_admin=True), ephemeral=True)

    logger.info("주제 찾기 사다리 명령어 등록 (공개=%s, AI=%s %s)", ui.is_open(), llm.BACKEND, "준비됨" if llm.ready() else "키 없음")


async def on_setup(bot):
    await store.load_all()
    if not llm.ready():
        logger.error("주제 찾기 사다리: ANTHROPIC_API_KEY 가 없어 AI 호출이 실패합니다.")
    if not store.using_sheets():
        logger.warning("주제 찾기 사다리: Google Sheets 미설정 — 재시작하면 학생 진행 상태가 사라집니다.")
