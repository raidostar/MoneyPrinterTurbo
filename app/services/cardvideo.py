"""
카드 대본을 영상 파일로 만든다.

카드마다 나레이션을 따로 합성한다. 통째로 한 번 읽히고 나서 카드 경계를 추정하면
글자 수 비율로 나누는 수밖에 없는데, 문장마다 읽는 속도가 달라 뒤로 갈수록 화면과
소리가 밀린다. 카드별로 만들면 그 카드의 실제 길이가 그대로 노출 시간이 된다.
"""

import os
from dataclasses import dataclass

from loguru import logger

from app.services import bgm as bgm_service
from app.services import cardnews, video as video_service, voice
from app.services.cardscript import CardScript

MAX_NARRATION_ATTEMPTS = 2
# 나레이션이 없는 카드도 잠깐은 보여 준다. 글이 있는데 지나쳐 버리면 안 된다.
FALLBACK_CARD_SECONDS = 2.5


@dataclass(frozen=True)
class CardVideoResult:
    video_path: str
    audio_path: str
    duration: float
    card_count: int


def _narrate(text: str, target_path: str, params) -> float:
    """
    카드 하나의 나레이션을 만들고 길이를 돌려준다. 실패하면 ``0``.

    한 장이 실패했다고 영상 전체를 버리지 않는다. 그 카드는 소리 없이 지나가고
    나머지는 그대로 나온다.
    """
    for attempt in range(MAX_NARRATION_ATTEMPTS):
        sub_maker = voice.tts(
            text=text,
            voice_name=params.voice_name,
            voice_rate=params.voice_rate,
            voice_file=target_path,
            voice_volume=params.voice_volume,
        )
        if sub_maker and os.path.exists(target_path):
            return voice.get_audio_duration(target_path)
        logger.warning(f"card narration failed, retrying... {attempt + 1}")
    return 0.0


def render_card_news(
    task_id: str, script: CardScript, params, output_dir: str
) -> CardVideoResult | None:
    """
    카드 대본으로 영상 하나를 만든다. 만들지 못하면 ``None``.

    ``params`` 는 기존 영상 파라미터를 그대로 쓴다. 음성, 배속, BGM 설정이 이미
    거기 있고, 카드뉴스라고 다른 값을 쓸 이유가 없다.
    """
    from moviepy import AudioFileClip, CompositeAudioClip, afx, concatenate_audioclips

    os.makedirs(output_dir, exist_ok=True)
    durations: list[float] = []
    narration_paths: list[str] = []

    for index, narration in enumerate(script.narrations, start=1):
        target = os.path.join(output_dir, f"card-{index:02d}.mp3")
        seconds = _narrate(narration, target, params) if narration.strip() else 0.0
        if seconds > 0:
            narration_paths.append(target)
            durations.append(seconds)
        else:
            # 소리 없이 지나가는 카드. 길이는 읽을 수 있을 만큼만 준다.
            durations.append(FALLBACK_CARD_SECONDS)

    if not durations:
        logger.error(f"card news has nothing to render: {task_id}")
        return None

    video_clip = cardnews.build_card_news_clip(script.cards, durations)
    audio_clip = None
    bgm_clip = None
    mixed = None
    try:
        if narration_paths:
            # 소리 없는 카드에도 영상은 흐르므로, 오디오는 이어 붙이기만 하고
            # 길이는 영상 쪽을 따른다.
            narration_clips = [AudioFileClip(path) for path in narration_paths]
            audio_clip = concatenate_audioclips(narration_clips).with_effects(
                [afx.MultiplyVolume(params.voice_volume)]
            )

        bgm_file = video_service.get_bgm_file(
            bgm_type=params.bgm_type, bgm_file=params.bgm_file
        )
        if bgm_file and bgm_service.should_use_bgm(params.bgm_type, params.bgm_volume):
            bgm_clip = AudioFileClip(bgm_file).with_effects(
                [
                    afx.MultiplyVolume(params.bgm_volume),
                    afx.AudioLoop(duration=video_clip.duration),
                    afx.AudioFadeOut(2),
                ]
            )

        tracks = [track for track in (audio_clip, bgm_clip) if track is not None]
        if tracks:
            mixed = CompositeAudioClip(tracks)
            video_clip = video_clip.with_audio(mixed)

        video_path = os.path.join(output_dir, "cardnews.mp4")
        video_clip.write_videofile(
            video_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            threads=params.n_threads or 2,
            temp_audiofile_path=output_dir,
            logger=None,
        )
    finally:
        for closable in (mixed, bgm_clip, audio_clip, video_clip):
            if closable is not None:
                closable.close()

    duration = sum(durations)
    logger.success(
        f"card news rendered: {video_path}, {len(script.cards)} cards, {duration:.1f}s"
    )
    return CardVideoResult(
        video_path=video_path,
        audio_path=narration_paths[0] if narration_paths else "",
        duration=duration,
        card_count=len(script.cards),
    )
