import asyncio
import base64
import os
import shutil
import unittest
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.utils import utils
from app.services import voice as vs
from app.services import task as task_service
from pydub import AudioSegment

temp_dir = utils.storage_dir("temp")

text_en = """
What is the meaning of life? 
This question has puzzled philosophers, scientists, and thinkers of all kinds for centuries. 
Throughout history, various cultures and individuals have come up with their interpretations and beliefs around the purpose of life. 
Some say it's to seek happiness and self-fulfillment, while others believe it's about contributing to the welfare of others and making a positive impact in the world. 
Despite the myriad of perspectives, one thing remains clear: the meaning of life is a deeply personal concept that varies from one person to another. 
It's an existential inquiry that encourages us to reflect on our values, desires, and the essence of our existence.
"""

text_zh = """
앞으로 사흘간 찬 공기가 자주 내려오겠고, 이틀 동안은 흐리고 비가 조금 오겠으니 우산을 챙기세요.
10~11일에는 계속 흐리고 비가 조금 오겠으며, 일교차가 작고 기온은 13~17도로 쌀쌀하겠습니다.
12일에는 잠시 날이 개겠으나 아침저녁으로는 선선하겠습니다.
"""

voice_rate=1.0
voice_volume=1.0
RUN_INTEGRATION_TESTS = os.environ.get("MPT_RUN_INTEGRATION_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}
                    
class TestVoiceService(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        self.loop.close()

    def test_get_all_azure_voices(self):
        voices = vs.get_all_azure_voices()
        # 데이터가 인라인 문자열에서 azure_voices.json 으로 옮겨졌다. 여전히 온전히 로딩되는지 확인한다
        self.assertEqual(len(voices), 331)
        # 결과는 "Name-Gender" 형식이어야 하고 정렬되어 있어야 한다
        self.assertEqual(voices, sorted(voices))
        for v in voices:
            self.assertTrue(v.endswith("-Male") or v.endswith("-Female"))

    def test_get_all_azure_voices_filtered(self):
        filtered = vs.get_all_azure_voices(filter_locals=["zh-CN", "en-US"])
        self.assertTrue(len(filtered) > 0)
        self.assertTrue(
            all(v.startswith(("zh-CN", "en-US")) for v in filtered)
        )

    def test_no_voice_tts_generates_silent_audio_and_subtitle_timeline(self):
        """
        나레이션 없음 모드는 외부 TTS provider 를 호출하지 않고 타임라인 자리표시자로 무음 오디오만 만든다.
        여기서는 FFmpeg 를 mock 해서 요청 파라미터, 출력 파일, legacy 자막 구조가 이후 영상 합성
        경로의 기대와 맞는지 검증한다.
        """

        def fake_run(command, capture_output, text, check):
            self.assertEqual(command[0], "/tmp/fake-ffmpeg")
            self.assertIn("anullsrc=r=44100:cl=mono", command)
            Path(command[-1]).write_bytes(b"fake-silent-mp3")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs.utils,
            "get_ffmpeg_binary",
            return_value="/tmp/fake-ffmpeg",
        ), patch.object(vs.subprocess, "run", side_effect=fake_run):
            voice_file = str(Path(tmp_dir) / "silent.mp3")
            sub_maker = vs.tts(
                # 전각 마침표(。) 로 문장을 나누는 동작을 검증하므로 CJK 문장 부호를 그대로 쓴다.
                text="第一句话。Second sentence.",
                voice_name=vs.NO_VOICE_NAME,
                voice_rate=1.0,
                voice_file=voice_file,
            )

            self.assertEqual(Path(voice_file).read_bytes(), b"fake-silent-mp3")

        self.assertIsNotNone(sub_maker)
        self.assertEqual(getattr(sub_maker, "subs", []), ["第一句话", "Second sentence"])
        self.assertEqual(len(getattr(sub_maker, "offset", [])), 2)
        self.assertGreater(vs.get_audio_duration(sub_maker), 0)

    def test_get_audio_duration_accepts_non_mp3_files(self):
        """
        사용자 오디오(custom_audio_file) 는 m4a/wav/aac 처럼 mp3 가 아닌 형식이 흔하다.
        get_audio_duration 이 확장자가 .mp3 가 아니라는 이유로 "Invalid target type" 을 내고 0 을
        반환해서는 안 되며, moviepy(ffmpeg) 가 실제 길이를 읽게 넘겨야 한다.
        """
        for path in ("custom-audio.m4a", "voice.wav", "clip.aac"):
            with patch.object(vs.os.path, "exists", return_value=True), \
                    patch.object(vs, "AudioFileClip") as mock_afc:
                mock_afc.return_value.__enter__.return_value.duration = 28.89
                self.assertEqual(vs.get_audio_duration(path), 28.89)
                mock_afc.assert_called_once_with(path)

    def test_get_audio_duration_missing_file_returns_zero(self):
        """오디오 파일이 없으면 예외를 던지거나 읽기에 실패하지 않고 안전하게 0 을 반환한다."""
        with patch.object(vs.os.path, "exists", return_value=False):
            self.assertEqual(vs.get_audio_duration("does-not-exist.m4a"), 0.0)

    def test_no_voice_alias_none_is_supported_temporarily(self):
        """
        PR #981 에서 쓰던 none sentinel 을 계속 받아 준다. API 를 직접 호출하던 소수 사용자가
        업그레이드 직후 깨지지 않게 하기 위해서다. 새 UI 와 새 코드는 no-voice 로 통일한다.
        """
        self.assertTrue(vs.is_no_voice("none"))
        self.assertTrue(vs.is_no_voice(vs.NO_VOICE_NAME))
        self.assertFalse(vs.is_no_voice(""))

    def test_no_voice_duration_estimates_non_ascii_languages(self):
        """
        나레이션 없음 모드에는 실제 TTS 오디오가 없으므로 대본 글자로 읽는 시간을 추정할 수밖에 없다.
        러시아어, 아랍어, 일본어 가나, 한글 같은 비 ASCII 텍스트도 추정에 참여해야 하며, 모두
        최소값인 3 초로 떨어져서는 안 된다.
        """
        russian_text = (
            "Это длинный тестовый сценарий без озвучки. "
            "Он должен получить достаточно времени для чтения субтитров."
        )
        arabic_text = "هذا اختبار طويل بدون تعليق صوتي، ويجب أن يحصل على وقت كاف لقراءة الترجمة."

        self.assertGreater(vs.estimate_no_voice_duration(russian_text), 8.0)
        self.assertGreater(vs.estimate_no_voice_duration(arabic_text), 8.0)

    def test_generate_silent_audio_rejects_missing_output_file(self):
        """
        FFmpeg 프로세스가 성공을 반환하더라도 출력 파일이 실제로 있고 비어 있지 않은지 확인해야 한다.
        그래야 이상을 TTS 단계에서 수렴시킬 수 있고, 이후 영상 합성 단계까지 끌고 가지 않는다.
        """
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs.utils,
            "get_ffmpeg_binary",
            return_value="/tmp/fake-ffmpeg",
        ), patch.object(
            vs.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            voice_file = str(Path(tmp_dir) / "missing-silent.mp3")

            self.assertFalse(vs.generate_silent_audio(3.0, voice_file))

    def test_empty_voice_name_does_not_enable_no_voice_mode(self):
        """
        voice 가 비어 있다는 것은 보통 설정 누락이나 파라미터 오류를 뜻하므로, 나레이션 없음 모드로
        자동 전환해서는 안 된다. 그러지 않으면 TTS 설정을 잘못 넣어도 '성공한' 무음 영상이 나와
        원인을 찾기가 더 어려워진다.
        """
        sentinel = object()

        with patch.object(vs, "azure_tts_v1", return_value=sentinel) as azure_tts_v1:
            result = vs.tts(
                text="empty voice should still use the default TTS path",
                voice_name="",
                voice_rate=1.0,
                voice_file="/tmp/empty-voice.mp3",
            )

        self.assertIs(result, sentinel)
        azure_tts_v1.assert_called_once()

    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_siliconflow(self):
        # SiliconFlow 의 API 키는 [siliconflow].api_key 에 있고, 런타임 코드도 config.siliconflow 에서
        # 읽는다. 여기서도 같은 설정 출처를 써야, 자격 증명을 제대로 넣었는데 테스트가 건너뛰어지는
        # 일이 없다.
        if not vs.config.siliconflow.get("api_key"):
            self.skipTest("siliconflow_api_key is not configured")

        voice_name = "siliconflow:FunAudioLLM/CosyVoice2-0.5B:alex-Male"
        voice_name = vs.parse_voice_name(voice_name)
        
        async def _do():
            parts = voice_name.split(":")
            if len(parts) >= 3:
                model = parts[1]
                # 성별 접미사를 제거한다. 예: "alex-Male" -> "alex"
                voice_with_gender = parts[2]
                voice = voice_with_gender.split("-")[0]
                # 전체 voice 파라미터를 만든다. 형식은 "model:voice"
                full_voice = f"{model}:{voice}"
                voice_file = f"{temp_dir}/tts-siliconflow-{voice}.mp3"
                subtitle_file = f"{temp_dir}/tts-siliconflow-{voice}.srt"
                sub_maker = vs.siliconflow_tts(
                    text=text_zh, model=model, voice=full_voice, voice_file=voice_file, voice_rate=voice_rate, voice_volume=voice_volume
                )
                if not sub_maker:
                    self.fail("siliconflow tts failed")
                vs.create_subtitle(sub_maker=sub_maker, text=text_zh, subtitle_file=subtitle_file)
                audio_duration = vs.get_audio_duration(sub_maker)
                print(f"voice: {voice_name}, audio duration: {audio_duration}s")
            else:
                self.fail("siliconflow invalid voice name")

        self.loop.run_until_complete(_do())
    
    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_azure_tts_v1(self):
        voice_name = "zh-CN-XiaoyiNeural-Female"
        voice_name = vs.parse_voice_name(voice_name)
        print(voice_name)
        
        voice_file = f"{temp_dir}/tts-azure-v1-{voice_name}.mp3"
        subtitle_file = f"{temp_dir}/tts-azure-v1-{voice_name}.srt"
        sub_maker = vs.azure_tts_v1(
            text=text_zh, voice_name=voice_name, voice_file=voice_file, voice_rate=voice_rate
        )
        if not sub_maker:
            self.fail("azure tts v1 failed")
        vs.create_subtitle(sub_maker=sub_maker, text=text_zh, subtitle_file=subtitle_file)
        audio_duration = vs.get_audio_duration(sub_maker)
        print(f"voice: {voice_name}, audio duration: {audio_duration}s")

    def test_azure_tts_v1_supports_legacy_edge_tts_without_boundary(self):
        """
        예전 edge_tts 의존성이 남아 있어도 Azure TTS V1 이 계속 동작하는지 검증한다.

        이 회귀 시나리오는 Windows 포터블 패키지 업데이트가 실패해 현장 환경이 예전 edge_tts 에
        머물러 있는 경우에 해당한다.
        1. `Communicate.__init__()` 이 `boundary` 를 받지 않는다
        2. 비동기 `stream()` 만 있고 `stream_sync()` 는 없다
        """

        class _LegacyCommunicate:
            def __init__(self, text, voice, rate="+0%"):
                self.text = text
                self.voice = voice
                self.rate = rate

            async def stream(self):
                yield {"type": "audio", "data": b"legacy-audio"}
                yield {
                    "type": "WordBoundary",
                    "offset": 0,
                    "duration": 10000000,
                    "text": "legacy",
                }

        class _FakeSubMaker:
            def __init__(self):
                self.events = []

            def feed(self, chunk):
                self.events.append(chunk)

            def get_srt(self):
                if not self.events:
                    return ""
                return "1\n00:00:00,000 --> 00:00:01,000\nlegacy\n"

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs.edge_tts, "Communicate", _LegacyCommunicate
        ), patch.object(vs.edge_tts, "SubMaker", _FakeSubMaker):
            voice_file = str(Path(tmp_dir) / "legacy-edge-tts.mp3")
            sub_maker = vs.azure_tts_v1(
                text="legacy edge tts compatibility",
                voice_name="zh-CN-XiaoyiNeural-Female",
                voice_file=voice_file,
                voice_rate=1.0,
            )

            self.assertIsNotNone(sub_maker)
            self.assertEqual(Path(voice_file).read_bytes(), b"legacy-audio")
            self.assertEqual(len(sub_maker.events), 1)
            self.assertEqual(sub_maker.events[0]["type"], "WordBoundary")

    def test_azure_tts_v1_times_out_hanging_stream_sync(self):
        """
        edge_tts 동기 스트림이 멈췄을 때 Azure TTS V1 이 빠르게 실패하는지 검증한다.

        실제 현장에서는 네트워크 이상, 서버 요청 제한, voice 언어와 텍스트 불일치 때문에
        `stream_sync()` 가 오래 반환하지 않아 WebUI 작업이 `start, voice name...` 에서 멈춘다.
        여기서는 블로킹하는 fake stream 으로 그 상황을 재현해, 타임아웃 보호가 함수를 끝내고
        None 을 반환하는지 확인한다.
        """

        class _HangingCommunicate:
            def __init__(self, text, voice, rate="+0%", boundary=None):
                self.text = text
                self.voice = voice
                self.rate = rate
                self.boundary = boundary

            def stream_sync(self):
                time.sleep(10)
                yield {"type": "audio", "data": b"unreachable"}

        class _FakeSubMaker:
            def feed(self, chunk):
                return None

            def get_srt(self):
                return ""

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs.edge_tts, "Communicate", _HangingCommunicate
        ), patch.object(vs.edge_tts, "SubMaker", _FakeSubMaker), patch.object(
            vs.config,
            "app",
            dict(vs.config.app, edge_tts_timeout=0.05),
        ):
            voice_file = Path(tmp_dir) / "hanging-edge-tts.mp3"
            started_at = time.monotonic()
            sub_maker = vs.azure_tts_v1(
                text="꽃이 피고 지는 영상을 만들어 줘",
                voice_name="en-AU-NatashaNeural-Female",
                voice_file=str(voice_file),
                voice_rate=1.0,
            )
            elapsed = time.monotonic() - started_at
            self.assertFalse(voice_file.exists())

        self.assertIsNone(sub_maker)
        self.assertLess(elapsed, 2)

    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_azure_tts_v2(self):
        if not vs.config.azure.get("speech_key") or not vs.config.azure.get("speech_region"):
            self.skipTest("Azure speech key or region is not configured")

        voice_name = "zh-CN-XiaoxiaoMultilingualNeural-V2-Female"
        voice_name = vs.parse_voice_name(voice_name)
        print(voice_name)

        async def _do():
            voice_file = f"{temp_dir}/tts-azure-v2-{voice_name}.mp3"
            subtitle_file = f"{temp_dir}/tts-azure-v2-{voice_name}.srt"
            sub_maker = vs.azure_tts_v2(
                text=text_zh,
                voice_name=voice_name,
                voice_file=voice_file,
                voice_rate=1.0,
            )
            if not sub_maker:
                self.fail("azure tts v2 failed")
            vs.create_subtitle(sub_maker=sub_maker, text=text_zh, subtitle_file=subtitle_file)
            audio_duration = vs.get_audio_duration(sub_maker)
            print(f"voice: {voice_name}, audio duration: {audio_duration}s")

        self.loop.run_until_complete(_do())

    def test_azure_tts_v2_ssml_applies_rate_and_escapes_text(self):
        """Azure V2 는 SSML 로 말하기 속도를 적용해야 하며, 사용자 대본이 XML 을 깨뜨려서도 안 된다."""
        ssml = vs._build_azure_v2_ssml(
            text='A < B & "quoted"',
            voice_name="zh-CN-XiaoxiaoMultilingualNeural",
            voice_rate=1.8,
        )

        self.assertIn('xml:lang="zh-CN"', ssml)
        self.assertIn('rate="1.8"', ssml)
        self.assertIn("A &lt; B &amp; \"quoted\"", ssml)

    def test_tts_forwards_rate_to_azure_v2(self):
        """통합 TTS 진입점이 Azure V2 로 분기할 때 voice_rate 를 잃어버려서는 안 된다."""
        voice_name = "zh-CN-XiaoxiaoMultilingualNeural-V2-Female"
        with patch.object(vs, "azure_tts_v2", return_value=object()) as mock_tts:
            result = vs.tts(
                text="말하기 속도 테스트",
                voice_name=voice_name,
                voice_rate=1.8,
                voice_file="/tmp/azure-v2-rate.mp3",
            )

        self.assertIsNotNone(result)
        mock_tts.assert_called_once_with(
            "말하기 속도 테스트",
            voice_name,
            "/tmp/azure-v2-rate.mp3",
            voice_rate=1.8,
        )

    def test_gemini_tts_uses_google_genai_and_compatible_submaker_fields(self):
        """
        edge_tts 7.x 환경에서도 Gemini TTS 가 프로젝트 호환 자막 구조를 반환하고,
        `subtitle_provider=edge` 의 자막 생성 경로가 그것을 바로 소비해 다시 Whisper 로
        되돌아가지 않는지 검증한다. 동시에 존재하지 않는 중첩 출력 디렉터리를 써서, API 나 CLI 가
        서비스를 직접 호출할 때 작업 디렉터리를 미리 만들지 않은 경계 상황도 덮는다.
        """

        class _InlineData:
            def __init__(self, data):
                self.data = data

        class _Part:
            def __init__(self, data):
                self.inline_data = _InlineData(data)

        class _Content:
            def __init__(self, data):
                self.parts = [_Part(data)]

        class _Candidate:
            def __init__(self, data):
                self.content = _Content(data)

        class _Response:
            def __init__(self, data):
                self.candidates = [_Candidate(data)]

        captured = {}

        class _FakeModels:
            def generate_content(self, **kwargs):
                captured.update(kwargs)
                tone = (
                    AudioSegment.silent(duration=1800)
                    .set_frame_rate(24000)
                    .set_channels(1)
                    .set_sample_width(2)
                )
                return _Response(tone.raw_data)

        class _FakeClient:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs
                self.models = _FakeModels()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                captured["closed"] = True

        temp_root = Path(tempfile.mkdtemp(prefix="gemini-tts-output-"))
        self.addCleanup(shutil.rmtree, temp_root, True)
        output_dir = temp_root / "nested" / "audio"
        voice_file = str(output_dir / "tts-gemini-Zephyr.mp3")
        subtitle_file = str(output_dir / "tts-gemini-Zephyr.srt")
        text = "Gemini subtitle generation should work now. Testing multiple lines."

        self.assertFalse(output_dir.exists())

        with patch("google.genai.Client", _FakeClient), patch.object(
            vs.config,
            "app",
            dict(vs.config.app, gemini_api_key="test-key"),
        ):
            sub_maker = vs.gemini_tts(
                text=text,
                voice_name="Zephyr",
                voice_rate=1.0,
                voice_file=voice_file,
            )

        self.assertIsNotNone(sub_maker)
        self.assertTrue(Path(voice_file).is_file())
        self.assertEqual(
            getattr(sub_maker, "subs", []),
            ["Gemini subtitle generation should work now", "Testing multiple lines"],
        )
        self.assertEqual(len(getattr(sub_maker, "offset", [])), 2)
        self.assertEqual(sub_maker.offset[0][0], 0)
        self.assertLess(sub_maker.offset[0][1], sub_maker.offset[1][1])
        self.assertEqual(captured["client_kwargs"], {"api_key": "test-key"})
        self.assertEqual(captured["model"], "gemini-2.5-flash-preview-tts")
        self.assertEqual(captured["contents"], text)
        self.assertEqual(captured["config"].response_modalities, ["AUDIO"])
        voice_config = captured["config"].speech_config.voice_config
        self.assertEqual(
            voice_config.prebuilt_voice_config.voice_name,
            "Zephyr",
        )
        self.assertTrue(captured["closed"])

        vs.create_subtitle(sub_maker=sub_maker, text=text, subtitle_file=subtitle_file)
        subtitle_content = Path(subtitle_file).read_text(encoding="utf-8")
        self.assertIn("Gemini subtitle generation should work now", subtitle_content)
        self.assertIn("Testing multiple lines", subtitle_content)

    def test_mimo_tts_uses_openai_compatible_audio_response(self):
        """
        Xiaomi MiMo TTS 가 OpenAI 호환 오디오 응답 구조를 소비할 수 있는지 검증한다.

        여기서는 fake OpenAI client 와 fake AudioSegment 로 실제 네트워크와 ffmpeg 를 대체해,
        런타임 코드가 합성할 텍스트를 assistant message 에 넣고 반환된 base64 WAV 오디오를
        이후 흐름이 쓰는 오디오 파일로 내보내는지 확인한다.
        """

        class _FakeAudio:
            def __init__(self):
                self.data = base64.b64encode(b"RIFF-fake-wav").decode("utf-8")

        class _FakeMessage:
            def __init__(self):
                self.audio = _FakeAudio()

        class _FakeChoice:
            def __init__(self):
                self.message = _FakeMessage()

        class _FakeCompletion:
            def __init__(self):
                self.choices = [_FakeChoice()]

        class _FakeCompletions:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return _FakeCompletion()

        class _FakeAudioSegment:
            def __len__(self):
                return 1800

            def export(self, output_file, format):
                Path(output_file).write_bytes(b"fake-mp3")

        fake_completions = _FakeCompletions()
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=fake_completions)
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs,
            "OpenAI",
            return_value=fake_client,
        ) as openai_client, patch(
            "pydub.AudioSegment.from_file",
            return_value=_FakeAudioSegment(),
        ), patch.object(
            vs.config,
            "app",
            dict(
                vs.config.app,
                mimo_api_key="mimo-key",
                mimo_base_url="https://api.xiaomimimo.com/v1",
                mimo_tts_model_name="mimo-v2.5-tts",
                mimo_tts_style_prompt="또렷한 한국어 나레이션으로 읽어 주세요.",
            ),
        ):
            voice_file = str(Path(tmp_dir) / "mimo-tts.mp3")
            sub_maker = vs.mimo_tts(
                # 전각 마침표(。) 기반 문장 분리를 함께 검증하므로 CJK 샘플을 유지한다.
                # voice_name 은 MiMo API 에 그대로 전달되는 음색 id 라 번역하지 않는다.
                text="小米语音合成测试。第二句话。",
                voice_name="冰糖",
                voice_rate=1.0,
                voice_file=voice_file,
                voice_volume=1.0,
            )
            generated_audio = Path(voice_file).read_bytes()

        openai_client.assert_called_once_with(
            api_key="mimo-key",
            base_url="https://api.xiaomimimo.com/v1",
        )
        self.assertEqual(fake_completions.kwargs["model"], "mimo-v2.5-tts")
        self.assertEqual(
            fake_completions.kwargs["messages"],
            [
                {"role": "user", "content": "또렷한 한국어 나레이션으로 읽어 주세요."},
                {"role": "assistant", "content": "小米语音合成测试。第二句话。"},
            ],
        )
        self.assertEqual(
            fake_completions.kwargs["audio"],
            {"format": "wav", "voice": "冰糖"},
        )
        self.assertEqual(generated_audio, b"fake-mp3")
        self.assertIsNotNone(sub_maker)
        self.assertEqual(getattr(sub_maker, "subs", []), ["小米语音合成测试", "第二句话"])
        self.assertEqual(len(getattr(sub_maker, "offset", [])), 2)

    def test_chatterbox_voice_helpers(self):
        """is_chatterbox_voice / get_chatterbox_voices basics and normalisation."""
        self.assertTrue(vs.is_chatterbox_voice("chatterbox:default-Female"))
        self.assertFalse(vs.is_chatterbox_voice("elevenlabs:abc:Rachel"))
        self.assertFalse(vs.is_chatterbox_voice(""))
        self.assertFalse(vs.is_chatterbox_voice(None))

        # list entries are normalised to the chatterbox:<name> dispatcher format,
        # and entries that are already prefixed are left untouched
        with patch.object(
            vs.config,
            "chatterbox",
            {"voices": ["narrator-Male", "chatterbox:host"]},
        ):
            self.assertEqual(
                vs.get_chatterbox_voices(),
                ["chatterbox:narrator-Male", "chatterbox:host"],
            )

        # a comma-separated string is also accepted (TOML-friendly)
        with patch.object(vs.config, "chatterbox", {"voices": "alpha, beta ,"}):
            self.assertEqual(
                vs.get_chatterbox_voices(),
                ["chatterbox:alpha", "chatterbox:beta"],
            )

        # with nothing configured the dropdown still gets a usable default
        with patch.object(vs.config, "chatterbox", {}):
            self.assertEqual(vs.get_chatterbox_voices(), ["chatterbox:default-Female"])

    def test_chatterbox_tts_posts_to_openai_compatible_endpoint(self):
        """Success path: POST /audio/speech, write audio, return legacy SubMaker."""

        class _FakeResponse:
            status_code = 200
            content = b"RIFF-fake-wav"
            text = ""

        class _FakeClip:
            duration = 3.5

            def close(self):
                pass

        captured = {}

        def _fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse()

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs.config,
            "chatterbox",
            {
                "base_url": "http://localhost:4123/v1/",
                "api_key": "secret",
                "model_id": "chatterbox",
            },
        ), patch.object(
            vs.requests, "post", side_effect=_fake_post
        ) as post, patch.object(
            vs, "AudioFileClip", return_value=_FakeClip()
        ):
            voice_file = str(Path(tmp_dir) / "chatterbox.mp3")
            sub_maker = vs.chatterbox_tts(
                text="Hello world. Second sentence.",
                voice="default",
                voice_file=voice_file,
                voice_rate=1.2,
                voice_volume=1.0,
            )
            generated_audio = Path(voice_file).read_bytes()

        post.assert_called_once()
        # trailing slash on base_url is stripped before appending /audio/speech
        self.assertEqual(captured["url"], "http://localhost:4123/v1/audio/speech")
        self.assertEqual(captured["json"]["model"], "chatterbox")
        self.assertEqual(captured["json"]["voice"], "default")
        self.assertEqual(captured["json"]["input"], "Hello world. Second sentence.")
        self.assertAlmostEqual(captured["json"]["speed"], 1.2)
        # api_key is forwarded as a bearer token
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer secret")
        # volume is intentionally not part of the OpenAI speech payload
        self.assertNotIn("volume", captured["json"])
        self.assertEqual(generated_audio, b"RIFF-fake-wav")
        self.assertIsNotNone(sub_maker)
        self.assertTrue(getattr(sub_maker, "subs", []))

    def test_chatterbox_tts_requires_base_url(self):
        """Missing base_url short-circuits without any network call."""
        with patch.object(
            vs.config, "chatterbox", {"base_url": ""}
        ), patch.object(vs.requests, "post") as post:
            result = vs.chatterbox_tts(
                text="hi", voice="default", voice_file="unused.mp3"
            )
        self.assertIsNone(result)
        post.assert_not_called()

    def test_chatterbox_tts_returns_none_on_http_error(self):
        """A non-200 response is retried up to 3 times, then fails to None."""

        class _FakeResponse:
            status_code = 500
            content = b""
            text = "boom"

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            vs.config, "chatterbox", {"base_url": "http://localhost:4123/v1"}
        ), patch.object(
            vs.requests, "post", return_value=_FakeResponse()
        ) as post:
            voice_file = str(Path(tmp_dir) / "chatterbox.mp3")
            result = vs.chatterbox_tts(
                text="hi", voice="default", voice_file=voice_file
            )
        self.assertIsNone(result)
        self.assertEqual(post.call_count, 3)

    def test_generate_subtitle_keeps_edge_provider_for_gemini_legacy_submaker(self):
        """
        Gemini TTS 가 반환한 legacy 자막 구조가 edge provider 에서 바로 SRT 를 만들어 내고,
        매칭 실패로 Whisper 로 되돌아가지 않는지 검증한다.
        """
        script = "Gemini subtitle generation should work now. Testing multiple lines."
        sub_maker = vs.populate_legacy_submaker_with_full_text(
            vs.ensure_legacy_submaker_fields(vs.SubMaker()),
            script,
            2.4,
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            task_service.config,
            "app",
            dict(task_service.config.app, subtitle_provider="edge"),
        ), patch("app.services.subtitle.create") as whisper_create, patch(
            "app.utils.utils.task_dir",
            lambda tid="": str(Path(tmp_dir) / tid) if tid else str(Path(tmp_dir)),
        ):
            task_id = "gemini-subtitle-edge-task"
            Path(tmp_dir, task_id).mkdir(parents=True, exist_ok=True)
            subtitle_path = task_service.generate_subtitle(
                task_id=task_id,
                params=type("Params", (), {"subtitle_enabled": True})(),
                video_script=script,
                sub_maker=sub_maker,
                audio_file="",
            )

            self.assertTrue(subtitle_path.endswith("subtitle.srt"))
            self.assertTrue(Path(subtitle_path).exists())
            self.assertFalse(whisper_create.called)
            subtitle_content = Path(subtitle_path).read_text(encoding="utf-8")
            self.assertIn("Gemini subtitle generation should work now", subtitle_content)
            self.assertIn("Testing multiple lines", subtitle_content)

    def test_script_split_keeps_thousand_separator_comma(self):
        """
        Edge TTS 는 "1,000 years" 를 연속된 텍스트로 반환한다. 대본을 문장으로 나눌 때 숫자 사이의
        영문 쉼표를 문장 경계로 봐서는 안 된다. 그러지 않으면 issue #894 처럼 자막 병합에서
        sub_items 수가 script_lines 보다 적어지고 잘못 Whisper 로 되돌아간다.
        """
        text = (
            "It takes about 1,000 years for a single drop of water to finish "
            "the whole trip!"
        )

        self.assertEqual(
            utils.split_string_by_punctuations(text),
            [
                (
                    "It takes about 1,000 years for a single drop of water to finish "
                    "the whole trip"
                )
            ],
        )

    def test_edge_cue_aggregation_handles_thousand_separator_comma(self):
        """
        issue #894 의 핵심 형태를 재현한다. Edge cues 의 마지막 문장이 `1,000 years` 를 포함한
        연속 텍스트로 반환된다. 대본 문장 분리는 cues 병합 결과와 일치해야 하며, 이것을 자막
        두 개로 쪼개서는 안 된다.
        """
        text = (
            "The ocean isn't just sitting stil, it moves around the world like a massive "
            "amusement park ride! Cold water at the North and South Poles sinks to the "
            "bottom because it is heavy and salty. At the same time, warm water from the "
            "sunny equator flows along the top to take its place. This creates a giant "
            "underwater conveyor belt that travels all the way around the Earth. It takes "
            "about 1,000 years for a single drop of water to finish the whole trip!"
        )
        script_lines = utils.split_string_by_punctuations(text)
        cues = []
        for index, line in enumerate(script_lines):
            # Edge 의 cue content 에는 대본의 공백과 문장 부호 배치가 없는 경우가 많다. 여기서는
            # 공백을 지워 더 엄격한 매칭 상황을 흉내 낸다.
            cues.append(
                SimpleNamespace(
                    content=line.replace(" ", ""),
                    start=timedelta(seconds=index),
                    end=timedelta(seconds=index + 0.8),
                )
            )
        sub_maker = SimpleNamespace(cues=cues)

        sub_items = vs._build_subtitle_items_from_edge_cues(sub_maker, script_lines)

        self.assertEqual(len(sub_items), len(script_lines))
        self.assertIn("1,000 years", sub_items[-1])

    def test_script_split_supports_arabic_punctuation(self):
        """
        아랍어 대본은 ، ؛ ؟ 를 자연스러운 문장 분리 부호로 자주 쓴다. 문장 분리 단계에서 이 부호들을
        인식해야 하며, 그러지 않으면 edge-tts cue 의 끊김 경계와 대본 줄 경계가 어긋난다.
        """
        text = "مرحبا بالعالم، كيف حالك؟ هذا اختبار؛ يعمل بشكل جيد."

        self.assertEqual(
            utils.split_string_by_punctuations(text),
            [
                "مرحبا بالعالم",
                "كيف حالك",
                "هذا اختبار",
                "يعمل بشكل جيد",
            ],
        )

    def test_match_script_line_normalizes_arabic_letter_forms(self):
        """
        edge-tts 는 아랍어의 서로 다른 글자 형태를 정규화하거나 발음 부호, Tatweel 이 붙은 cue
        텍스트를 반환할 수 있다. 매칭은 이를 허용하되 최종 자막은 원본 대본 문구를 유지해야 한다.
        """
        script_lines = ["أهلاً وسهلاً بك في المدرسة"]

        matched = vs._match_script_line(
            script_lines,
            "اهلا وسهلا بك في المدرسه",
            0,
        )

        self.assertEqual(matched, script_lines[0])

    def test_edge_cue_aggregation_handles_arabic_variant_forms(self):
        """
        아랍어 자막 실패의 핵심 경로를 재현한다. 대본에는 أ/ة 같은 글자 형태가 들어 있고 edge cue 는
        ا/ه 같은 정규화 형태를 반환할 때도, 병합은 온전한 자막을 만들어 Whisper 로 되돌아가지 않아야 한다.
        """
        text = "أهلاً وسهلاً بك في المدرسة؟ هذا اختبار رائع، شكراً لك."
        script_lines = utils.split_string_by_punctuations(text)
        cue_texts = [
            "اهلا وسهلا بك في المدرسه",
            "هذا اختبار رائع",
            "شكرا لك",
        ]
        sub_maker = SimpleNamespace(
            cues=[
                SimpleNamespace(
                    content=cue_text,
                    start=timedelta(seconds=index),
                    end=timedelta(seconds=index + 0.8),
                )
                for index, cue_text in enumerate(cue_texts)
            ]
        )

        sub_items = vs._build_subtitle_items_from_edge_cues(sub_maker, script_lines)

        self.assertEqual(len(sub_items), len(script_lines))
        self.assertIn("أهلاً وسهلاً بك في المدرسة", sub_items[0])
        self.assertIn("شكراً لك", sub_items[-1])

    def test_create_subtitle_ignores_markdown_separator_lines(self):
        """
        사용자가 직접 쓴 대본에는 `---` 같은 Markdown 구분선이 들어갈 수 있다. TTS 는 이런 기호 줄을
        읽지 않으므로 자막 병합도 이를 목표 자막 줄로 봐서는 안 된다. 그러지 않으면 이후 실제 자막이
        막히고 Whisper 로 되돌아간다.
        """
        text = "첫 번째 문단\n---\n두 번째 문단"
        sub_maker = SimpleNamespace(
            cues=[
                SimpleNamespace(
                    content="첫 번째 문단",
                    start=timedelta(seconds=0),
                    end=timedelta(seconds=0.8),
                ),
                SimpleNamespace(
                    content="두 번째 문단",
                    start=timedelta(seconds=1),
                    end=timedelta(seconds=1.8),
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            vs.create_subtitle(
                sub_maker=sub_maker,
                text=text,
                subtitle_file=str(subtitle_file),
            )

            subtitle_content = subtitle_file.read_text(encoding="utf-8")

        self.assertIn("첫 번째 문단", subtitle_content)
        self.assertIn("두 번째 문단", subtitle_content)
        self.assertNotIn("---", subtitle_content)
        self.assertNotIn("00:00:00,000 --> 00:00:00,000", subtitle_content)

    def test_create_subtitle_ignores_markdown_underscore_marks(self):
        """
        `_` 는 사용자가 Markdown 강조 표기로 자주 쓰지만, TTS 가 반환하는 cue 에는 보통 이런 서식
        기호가 없다. 매칭에서 `_` 를 무시해야 빈 자막이 생기거나 Whisper 로 되돌아가지 않는다.
        """
        text = "이것은_a_테스트입니다."
        sub_maker = SimpleNamespace(
            cues=[
                SimpleNamespace(
                    content="이것은a테스트입니다",
                    start=timedelta(seconds=0),
                    end=timedelta(seconds=0.8),
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            vs.create_subtitle(
                sub_maker=sub_maker,
                text=text,
                subtitle_file=str(subtitle_file),
            )

            subtitle_content = subtitle_file.read_text(encoding="utf-8")

        self.assertIn("이것은a테스트입니다", subtitle_content)
        self.assertNotIn("이것은_a_테스트입니다", subtitle_content)
        self.assertNotIn("00:00:00,000 --> 00:00:00,000", subtitle_content)

    def test_convert_rate_to_percent_signs_zero_rate(self):
        # Rates near but not exactly 1.0 round to 0 percent. edge-tts rejects
        # an unsigned "0%" (ValueError: Invalid rate '0%'), so the helper must
        # emit a sign-prefixed "+0%". Regression test for that crash.
        self.assertEqual(vs.convert_rate_to_percent(1.0), "+0%")
        self.assertEqual(vs.convert_rate_to_percent(1.004), "+0%")
        self.assertEqual(vs.convert_rate_to_percent(0.997), "+0%")
        self.assertEqual(vs.convert_rate_to_percent(1.5), "+50%")
        self.assertEqual(vs.convert_rate_to_percent(0.8), "-20%")

    def test_convert_rate_to_percent_invalid_values_default_to_normal(self):
        # API 나 배치 스크립트가 빈 속도를 0, None, 빈 문자열로 넘길 수 있다. 이런 값 때문에
        # edge-tts 가 -100% 를 받거나 예외가 나서는 안 되고, 정상 속도로 처리돼야 한다.
        self.assertEqual(vs.convert_rate_to_percent(0), "+0%")
        self.assertEqual(vs.convert_rate_to_percent(0.0), "+0%")
        self.assertEqual(vs.convert_rate_to_percent(None), "+0%")
        self.assertEqual(vs.convert_rate_to_percent(""), "+0%")


class TestElevenLabsVoice(unittest.TestCase):

    def test_is_elevenlabs_voice_true(self):
        self.assertTrue(vs.is_elevenlabs_voice("elevenlabs:pNInz6obpgDQGcFmaJgB:Adam"))

    def test_is_elevenlabs_voice_false_azure(self):
        self.assertFalse(vs.is_elevenlabs_voice("zh-CN-XiaoxiaoNeural-Female"))

    def test_is_elevenlabs_voice_false_siliconflow(self):
        self.assertFalse(vs.is_elevenlabs_voice("siliconflow:model:voice-Male"))

    def test_is_elevenlabs_voice_empty(self):
        self.assertFalse(vs.is_elevenlabs_voice(""))

    def test_is_elevenlabs_voice_none(self):
        self.assertFalse(vs.is_elevenlabs_voice(None))

    def test_get_elevenlabs_voices_empty_api_key(self):
        result = vs.get_elevenlabs_voices("")
        self.assertEqual(result, [])

    @patch("app.services.voice.requests.get")
    def test_get_elevenlabs_voices_success(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "voices": [
                {"voice_id": "abc123", "name": "Adam"},
                {"voice_id": "def456", "name": "Rachel"},
            ]
        }
        result = vs.get_elevenlabs_voices("fake-api-key")
        self.assertEqual(result, [
            "elevenlabs:abc123:Adam",
            "elevenlabs:def456:Rachel",
        ])
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        self.assertIn("xi-api-key", call_kwargs.kwargs.get("headers", {}))

    @patch("app.services.voice.requests.get")
    def test_get_elevenlabs_voices_http_error(self, mock_get):
        mock_get.return_value.status_code = 401
        mock_get.return_value.text = "Unauthorized"
        result = vs.get_elevenlabs_voices("bad-key")
        self.assertEqual(result, [])

    @patch("app.services.voice.requests.get")
    def test_get_elevenlabs_voices_network_error(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.ConnectionError("timeout")
        result = vs.get_elevenlabs_voices("fake-key")
        self.assertEqual(result, [])

    @patch("app.services.voice.requests.post")
    @patch("app.services.voice.AudioFileClip")
    @patch("app.services.voice.config")
    def test_elevenlabs_tts_success(self, mock_config, mock_clip_cls, mock_post):
        mock_config.elevenlabs.get.return_value = "fake-api-key"
        mock_post.return_value.status_code = 200
        mock_post.return_value.content = b"fake-mp3-bytes"
        mock_clip_cls.return_value.duration = 3.0
        mock_clip_cls.return_value.close = lambda: None

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            out_path = f.name

        try:
            result = vs.elevenlabs_tts("Hello world", "abc123", out_path)
            self.assertIsNotNone(result)
            self.assertTrue(hasattr(result, "subs"))
            self.assertTrue(hasattr(result, "offset"))
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    @patch("app.services.voice.config")
    def test_elevenlabs_tts_no_api_key(self, mock_config):
        mock_config.elevenlabs.get.return_value = ""
        result = vs.elevenlabs_tts("Hello", "abc123", "/tmp/test.mp3")
        self.assertIsNone(result)

    @patch("app.services.voice.config")
    def test_elevenlabs_tts_empty_text(self, mock_config):
        mock_config.elevenlabs.get.return_value = "fake-key"
        result = vs.elevenlabs_tts("  ", "abc123", "/tmp/test.mp3")
        self.assertIsNone(result)


if __name__ == "__main__":
    # python -m unittest test.services.test_voice.TestVoiceService.test_azure_tts_v1
    # python -m unittest test.services.test_voice.TestVoiceService.test_azure_tts_v2
    unittest.main() 


class TestDefaultVoiceRate(unittest.TestCase):
    """
    합성 음성은 등속으로 읽어서 실제 속도보다 느리게 들린다. 쇼츠는 한 박자만
    늘어져도 넘긴다.
    """

    def test_new_work_runs_faster_than_real_time(self):
        from app.models.schema import DEFAULT_VOICE_RATE, VideoParams

        self.assertGreater(DEFAULT_VOICE_RATE, 1.0)
        self.assertEqual(VideoParams(video_subject="주제").voice_rate, DEFAULT_VOICE_RATE)

    def test_the_speed_the_screen_offers_includes_the_default(self):
        """
        고를 수 없는 값을 기본값으로 두면 화면이 열릴 때 목록에 없는 값이라
        멋대로 다른 값으로 바뀐다.
        """
        from app.models.schema import DEFAULT_VOICE_RATE

        offered = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0]
        self.assertIn(DEFAULT_VOICE_RATE, offered)

    def test_the_bot_uses_the_same_default(self):
        """봇으로 만든 영상만 느리면 채널 안에서 속도가 들쭉날쭉해진다."""
        from unittest.mock import patch

        from app.models.schema import DEFAULT_VOICE_RATE
        from app.services import telegram_bot as bot

        with patch.dict(bot.config.ui, {}, clear=True):
            self.assertEqual(bot._build_params("주제", "대본").voice_rate, DEFAULT_VOICE_RATE)


class TestTimelineCoversTheScript(unittest.TestCase):
    """
    자막은 타임라인 조각을 쌓아 대본 문장과 맞아떨어질 때 한 줄로 확정한다. 스트림이
    도중에 끊겨 뒷부분이 없으면 남은 문장이 하나도 안 맞아, 자막 파일이 통째로
    빠지고 자막 없는 영상이 나간다. 실제로 같은 대본에서 조각이 다섯 개만 온 적이
    있고, 그때 열일곱 문장 중 하나만 맞았다.
    """

    def _sub_maker(self, chunks):
        from types import SimpleNamespace

        return SimpleNamespace(
            cues=[SimpleNamespace(content=text) for text in chunks],
            get_srt=lambda: "1\n00:00:00,000 --> 00:00:01,000\nx\n",
        )

    def test_a_complete_timeline_passes(self):
        from app.services.voice import _cues_cover_text

        text = "양치 끝냈는데, 고춧가루 또 나왔음. 치실은 귀찮았음."
        chunks = ["양치", "끝냈는데", "고춧가루", "또", "나왔음", "치실은", "귀찮았음"]
        self.assertTrue(_cues_cover_text(self._sub_maker(chunks), text))

    def test_a_timeline_that_stops_early_is_refused(self):
        from app.services.voice import _cues_cover_text

        text = "양치 끝냈는데, 고춧가루 또 나왔음. 치실은 귀찮았음."
        self.assertFalse(
            _cues_cover_text(self._sub_maker(["양치", "끝냈는데", "고춧가루"]), text)
        )

    def test_an_old_style_maker_without_cues_is_left_alone(self):
        """
        cue 가 없는 예전 구조는 타임라인을 다르게 쌓는다. 이 검사로 막으면 멀쩡히
        돌던 제공자가 매번 재시도에 걸린다.
        """
        from types import SimpleNamespace

        from app.services.voice import _cues_cover_text

        self.assertTrue(_cues_cover_text(SimpleNamespace(cues=[]), "아무 말"))
        self.assertTrue(_cues_cover_text(SimpleNamespace(), "아무 말"))

    def test_arabic_letter_shapes_are_accepted_the_same_way(self):
        """
        문장 매칭은 마지막에 아랍어 글자 형태를 맞춰 본다. 여기만 더 엄격하면,
        자막은 만들 수 있는 대본인데도 세 번을 다 다시 부른다.
        """
        from app.services.voice import _cues_cover_text

        self.assertTrue(
            _cues_cover_text(self._sub_maker(["اهلا", "بك"]), "أهلاً بك")
        )

    def test_a_short_timeline_is_retried(self):
        from unittest.mock import patch

        from app.services import voice as voice_module

        text = "첫 문장임. 두 번째 문장임."
        short = self._sub_maker(["첫"])
        full = self._sub_maker(["첫", "문장임", "두", "번째", "문장임"])
        makers = [short, full]

        def fake_stream(communicate, handle, timeout_seconds=None):
            pass

        with (
            patch.object(voice_module, "create_edge_tts_communicate"),
            patch.object(voice_module, "stream_edge_tts_chunks", side_effect=fake_stream),
            patch.object(voice_module.edge_tts, "SubMaker", side_effect=lambda: makers.pop(0)),
            patch.object(voice_module, "ensure_file_path_exists"),
            patch("builtins.open", unittest.mock.mock_open()),
        ):
            result = voice_module.azure_tts_v1(
                text=text, voice_name="ko-KR-X", voice_rate=1.3, voice_file="/tmp/x.mp3"
            )

        self.assertIs(result, full)

    def test_a_timeline_is_never_paired_with_another_attempt_audio(self):
        """
        시도마다 같은 파일을 지우고 새로 쓴다. 첫 시도의 타임라인을 들고 있다가
        세 번째 시도가 예외로 끝나면, 파일에는 세 번째의 반쪽 소리가 남았는데
        첫 번째의 타임라인을 돌려주게 된다. 그 둘은 짝이 아니다.
        """
        from unittest.mock import patch

        from app.services import voice as voice_module

        short = self._sub_maker(["첫"])
        makers = [short, short, short]
        attempts = {"n": 0}

        def sometimes_explode(communicate, handle, timeout_seconds=None):
            attempts["n"] += 1
            if attempts["n"] > 1:
                raise RuntimeError("stream died")

        with (
            patch.object(voice_module, "create_edge_tts_communicate"),
            patch.object(voice_module, "stream_edge_tts_chunks", side_effect=sometimes_explode),
            patch.object(voice_module.edge_tts, "SubMaker", side_effect=lambda: makers.pop(0)),
            patch.object(voice_module, "ensure_file_path_exists"),
            patch.object(voice_module.os.path, "exists", return_value=False),
            patch("builtins.open", unittest.mock.mock_open()),
        ):
            result = voice_module.azure_tts_v1(
                text="첫 문장임. 두 번째 문장임.",
                voice_name="ko-KR-X",
                voice_rate=1.3,
                voice_file="/tmp/x.mp3",
            )

        self.assertIsNone(result)

    def test_audio_still_comes_back_when_the_timeline_never_covers(self):
        """자막이 빠지는 것보다 소리가 없는 편이 나쁘다."""
        from unittest.mock import patch

        from app.services import voice as voice_module

        short = self._sub_maker(["첫"])
        with (
            patch.object(voice_module, "create_edge_tts_communicate"),
            patch.object(voice_module, "stream_edge_tts_chunks"),
            patch.object(voice_module.edge_tts, "SubMaker", return_value=short),
            patch.object(voice_module, "ensure_file_path_exists"),
            patch("builtins.open", unittest.mock.mock_open()),
        ):
            result = voice_module.azure_tts_v1(
                text="첫 문장임. 두 번째 문장임.",
                voice_name="ko-KR-X",
                voice_rate=1.3,
                voice_file="/tmp/x.mp3",
            )

        self.assertIs(result, short)


class TestSubtitleLineBreaks(unittest.TestCase):
    """
    한 줄에 안 들어가는 자막은 렌더러가 접고, 마지막 한 단어만 다음 줄에 남아
    어정쩡하게 보인다. "그날 외장하드 하나 두고 작업 순서를 / 바꿨음" 처럼.
    """

    def test_a_line_that_fits_is_left_alone(self):
        from app.services.voice import split_for_one_line

        for line in ("이게 자리값 하더라", "편집 끝내고 내보내기 눌렀는데"):
            with self.subTest(line=line):
                self.assertEqual(split_for_one_line(line), [line])

    def test_a_long_line_is_split(self):
        from app.services.voice import MAX_SUBTITLE_LINE_LENGTH, _display_width, split_for_one_line

        chunks = split_for_one_line("그날 외장하드 하나 두고 작업 순서를 바꿨음")

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(_display_width(chunk), MAX_SUBTITLE_LINE_LENGTH)

    def test_the_split_does_not_strand_one_word(self):
        """
        앞에서부터 채우면 뒤 조각에 한 단어만 남아, 접혔을 때와 똑같이 보인다.
        """
        from app.services.voice import _display_width, split_for_one_line

        for line in (
            "그날 외장하드 하나 두고 작업 순서를 바꿨음",
            "집 오면 촬영 원본부터 날짜별 폴더로 옮김",
            "촬영 자주 나가서 원본이 매주 쌓이는 사람",
        ):
            with self.subTest(line=line):
                head, tail = split_for_one_line(line)
                self.assertGreaterEqual(_display_width(tail), 6)
                # 한쪽이 다른 쪽의 세 배를 넘으면 나눈 의미가 없다.
                self.assertLess(max(_display_width(head), _display_width(tail)),
                                min(_display_width(head), _display_width(tail)) * 3)

    def test_nothing_is_lost_or_added(self):
        """자막은 소리와 맞춰야 한다. 글자가 바뀌면 그 자리에서 매칭이 깨진다."""
        from app.services.voice import split_for_one_line

        line = "급하게 파일 지우다가 전날 촬영본까지 같이 지웠음"
        self.assertEqual(" ".join(split_for_one_line(line)), line)

    def test_a_break_lands_where_speech_would_pause(self):
        """
        조사나 연결어미 뒤는 소리 내어 읽어도 숨을 쉬는 자리다. 그냥 가운데서
        자르면 "내보내기 전에 뭘 / 지울지" 처럼 말이 끊기지 않는 곳에서 끊긴다.
        """
        from app.services.voice import split_for_one_line

        head, _ = split_for_one_line("내보내기 전에 뭘 지울지 안 헤매게 됐음")
        self.assertEqual(head, "내보내기 전에")

    def test_a_word_too_long_to_split_is_kept_whole(self):
        """나눌 자리가 없으면 그대로 둔다. 낱말 가운데를 자를 수는 없다."""
        from app.services.voice import split_for_one_line

        line = "한단어인데아주긴경우는나눌자리가없음"
        self.assertEqual(split_for_one_line(line), [line])

    def test_short_pieces_are_not_made(self):
        """
        두세 글자짜리 자막이 스쳐 지나가면 읽을 수 없다. 가운데에 가까운 자리가
        그런 조각을 만들면, 조금 치우쳐도 다른 자리를 골라야 한다.
        """
        from app.services.voice import MIN_SUBTITLE_LINE_LENGTH, _display_width, split_for_one_line

        # 나눌 수 있는 자리가 맨 앞뿐이고, 거기서 자르면 두 글자만 남는다.
        # 그럴 바에는 안 나누는 편이 낫다.
        for line in ("가나 다라마바사아자차카타파하거너더러머버서어저", "머버서어저 가"):
            with self.subTest(line=line):
                for chunk in split_for_one_line(line):
                    self.assertGreaterEqual(
                        _display_width(chunk), MIN_SUBTITLE_LINE_LENGTH
                    )

    def test_a_line_far_over_the_limit_is_split_more_than_once(self):
        """
        한 번만 나누면 절반도 여전히 한 줄에 안 들어간다. 그러면 그 조각이 다시
        접히고, 고치려던 모양 그대로 나온다.
        """
        from app.services.voice import MAX_SUBTITLE_LINE_LENGTH, _display_width, split_for_one_line

        line = "촬영 끝나고 집에 오면 원본부터 날짜별 폴더로 옮기고 편집할 것만 노트북에 남겨둠"
        chunks = split_for_one_line(line)

        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(_display_width(chunk), MAX_SUBTITLE_LINE_LENGTH)

    def test_han_and_kana_take_the_full_room(self):
        """
        한글만 넓다고 세면 한자와 가나가 로마자 취급을 받아, 그쪽 자막은 두 배로
        길어진 뒤에야 나뉜다.
        """
        from app.services.voice import _display_width

        self.assertEqual(_display_width("東京"), _display_width("도쿄"))
        self.assertEqual(_display_width("データ"), _display_width("데이터"))
        self.assertGreater(_display_width("東京"), _display_width("ab"))

    def test_latin_letters_take_half_the_room(self):
        """
        로마자는 글자 폭이 한글의 절반쯤이다. 같은 글자 수로 재면 영어 자막이
        멀쩡한데도 자꾸 나뉜다.
        """
        from app.services.voice import split_for_one_line

        # 한글 열여덟 자보다 글자 수는 많지만 화면에서는 더 좁다.
        self.assertEqual(
            split_for_one_line("moving the raw files first"),
            ["moving the raw files first"],
        )


class TestSubtitleFallsBackToWholeSentences(unittest.TestCase):
    """
    나눈 자리가 타임라인 조각 하나의 가운데를 지나면 매칭이 안 된다. 그때 그냥
    실패하면 자막이 통째로 빠진다 — 나누기 전에는 나오던 자막이다.
    """

    def _sub_maker(self, chunks):
        from datetime import timedelta
        from types import SimpleNamespace

        cues = []
        for index, text in enumerate(chunks):
            cues.append(
                SimpleNamespace(
                    content=text,
                    start=timedelta(seconds=index),
                    end=timedelta(seconds=index + 1),
                )
            )
        return SimpleNamespace(cues=cues, get_srt=lambda: "x")

    def test_the_split_is_tried_first_and_the_whole_sentence_second(self):
        """
        나눈 것을 먼저 시도하고, 안 맞으면 원래 문장으로 돌아가야 한다. 순서가
        뒤바뀌면 나누는 일이 아무 효과가 없고, 되돌아갈 곳이 없으면 자막이
        통째로 빠진다.
        """
        from app.services.voice import _subtitle_line_candidates

        sentences = ["그날 외장하드 하나 두고 작업 순서를 바꿨음", "이게 자리값 하더라"]
        candidates = _subtitle_line_candidates(sentences)

        self.assertEqual(len(candidates), 2)
        self.assertGreater(len(candidates[0]), len(sentences))
        self.assertEqual(candidates[1], sentences)

    def test_nothing_to_split_means_nothing_to_fall_back_to(self):
        """이미 다 짧으면 후보가 하나다. 같은 것을 두 번 시도할 이유가 없다."""
        from app.services.voice import _subtitle_line_candidates

        sentences = ["이게 자리값 하더라", "편집할 파일만 남겨둠"]
        self.assertEqual(_subtitle_line_candidates(sentences), [sentences])

    def test_a_split_that_does_not_line_up_still_makes_subtitles(self):
        import tempfile
        from pathlib import Path

        from app.services import voice as voice_module

        # 타임라인이 문장 통째로 하나씩 온다. 나눈 조각과는 경계가 안 맞는다.
        text = "그날 외장하드 하나 두고 작업 순서를 바꿨음. 이게 자리값 하더라."
        maker = self._sub_maker(
            ["그날 외장하드 하나 두고 작업 순서를 바꿨음", "이게 자리값 하더라"]
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subtitle.srt"
            voice_module.create_subtitle(maker, text, str(target))

            self.assertTrue(target.exists())
            written = target.read_text(encoding="utf-8")

        # 나눈 것이 안 맞았으므로 원래 문장 그대로, 두 줄이어야 한다.
        self.assertEqual(written.count("-->"), 2)
        self.assertIn("그날 외장하드 하나 두고 작업 순서를 바꿨음", written)

    def test_a_split_that_lines_up_is_used(self):
        import tempfile
        from pathlib import Path

        from app.services import voice as voice_module

        text = "그날 외장하드 하나 두고 작업 순서를 바꿨음."
        maker = self._sub_maker(
            ["그날", "외장하드", "하나", "두고", "작업", "순서를", "바꿨음"]
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subtitle.srt"
            voice_module.create_subtitle(maker, text, str(target))
            written = target.read_text(encoding="utf-8")

        # 나뉘어 각자 제 시간을 갖는다. 통째로 한 줄이면 두 조각이 다 들어 있는
        # 것처럼 보이므로, 자막이 몇 개인지로 확인한다.
        self.assertEqual(written.count("-->"), 2)
        self.assertIn("그날 외장하드 하나 두고\n", written)
        self.assertIn("작업 순서를 바꿨음\n", written)
