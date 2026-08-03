"""
카드 대본을 영상 파일로 만든다.

카드마다 나레이션을 따로 합성한다. 통째로 한 번 읽히고 나서 카드 경계를 추정하면
글자 수 비율로 나누는 수밖에 없는데, 문장마다 읽는 속도가 달라 뒤로 갈수록 화면과
소리가 밀린다. 카드별로 만들면 그 카드의 실제 길이가 그대로 노출 시간이 된다.
"""

import os
from contextlib import ExitStack
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
        # 음량은 아래에서 클립에 한 번만 건다. 여기서도 걸면 제공자에 따라 두 번
        # 곱해져, 0.2 를 넣은 사람이 0.04 를 듣게 된다.
        sub_maker = voice.tts(
            text=text,
            voice_name=params.voice_name,
            voice_rate=params.voice_rate,
            voice_file=target_path,
            voice_volume=1.0,
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
            continue

        # 소리 없이 지나가는 카드. 오디오 쪽에도 같은 길이의 무음을 넣어야 한다.
        # 빼 버리면 그 뒤 카드의 소리가 화면보다 먼저 나오고, 어긋남이 끝까지 남는다.
        silence = os.path.join(output_dir, f"card-{index:02d}-silence.mp3")
        if voice.generate_silent_audio(FALLBACK_CARD_SECONDS, silence):
            narration_paths.append(silence)
        else:
            logger.warning(f"could not pad the timeline for card {index}")
        durations.append(FALLBACK_CARD_SECONDS)

    if not durations:
        logger.error(f"card news has nothing to render: {task_id}")
        return None

    video_path = os.path.join(output_dir, "cardnews.mp4")
    # 원본 리더까지 확실히 닫는다. 합쳐진 클립만 닫으면 자식 리더가 남아 ffmpeg
    # 프로세스와 파일 잠금이 쌓인다.
    with ExitStack() as clips:
        video_clip = clips.enter_context(
            cardnews.build_card_news_clip(script.cards, durations)
        )
        audio_clip = None
        if narration_paths:
            narration_clips = [
                clips.enter_context(AudioFileClip(path)) for path in narration_paths
            ]
            audio_clip = clips.enter_context(
                concatenate_audioclips(narration_clips).with_effects(
                    [afx.MultiplyVolume(params.voice_volume)]
                )
            )

        bgm_clip = None
        bgm_file = video_service.get_bgm_file(
            bgm_type=params.bgm_type, bgm_file=params.bgm_file
        )
        if bgm_file and bgm_service.should_use_bgm(params.bgm_type, params.bgm_volume):
            bgm_source = clips.enter_context(AudioFileClip(bgm_file))
            bgm_clip = clips.enter_context(
                bgm_source.with_effects(
                    [
                        afx.MultiplyVolume(params.bgm_volume),
                        afx.AudioLoop(duration=video_clip.duration),
                        afx.AudioFadeOut(2),
                    ]
                )
            )

        tracks = [track for track in (audio_clip, bgm_clip) if track is not None]
        if tracks:
            mixed = clips.enter_context(CompositeAudioClip(tracks))
            video_clip = clips.enter_context(video_clip.with_audio(mixed))

        video_clip.write_videofile(
            video_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            threads=params.n_threads or 2,
            temp_audiofile_path=output_dir,
            logger=None,
        )

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
