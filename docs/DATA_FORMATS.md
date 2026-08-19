# 数据格式

以下示例均为虚构内容，不包含游戏原始对白。

## dialogue catalog（JSONL）

每行一条独立来源记录：

```json
{"line_id":"example-001","speaker_id":"npc-001","speaker":"示例侠客","text":"前路尚远，我们先在此歇息片刻。","speakable":true,"source":{"domain":"npc","namespace":"Example","key":"Line001"}}
```

`speaker_id` 是身份主键，`speaker` 是运行时显示名。不要用显示名代替身份主键。

## character profile

```json
{
  "character_id": "npc-001",
  "name": "示例侠客",
  "is_human": true,
  "gender": "male",
  "age_group": "young_adult",
  "role": "江湖游侠",
  "personality_traits": ["沉稳", "真诚"],
  "speaking_style": "表达自然，语速适中",
  "voice": {
    "pitch": "medium",
    "pace": "medium",
    "energy": "medium",
    "timbre": "清朗自然",
    "articulation": "清晰",
    "emotional_tone": "克制",
    "voice_design_prompt": "二十多岁的男性侠客，中音区，气息稳定，表达自然克制。"
  },
  "confidence": 0.9,
  "evidence": [
    {"source": "official_description", "fact": "行事沉稳"}
  ],
  "uncertainties": []
}
```

## voice group

一个声线组可以服务一个主要人物，也可以服务多个证据相近的泛化 NPC：

```json
{
  "voice_group_id": "0123456789abcdef",
  "name": "示例青年男声",
  "member_character_ids": ["npc-001"],
  "gender": "male",
  "age_group": "young_adult",
  "role": "江湖游侠",
  "voice": {
    "voice_design_prompt": "二十多岁的男性侠客，中音区，声音清朗，语速适中。"
  },
  "speakable_line_count": 12,
  "generation_status": "ready"
}
```

## anchor plan

```json
{
  "voice_group_id": "0123456789abcdef",
  "name": "示例青年男声",
  "voice_design_prompt": "二十多岁的男性侠客，中音区，声音清朗，语速适中。",
  "anchor_text": "前路尚远，我们先在此歇息片刻，再作打算。",
  "anchor_file": "offline/anchors/0123456789abcdef.wav",
  "anchor_text_source": "representative_game_dialogue"
}
```

## dialogue job（JSONL）

```json
{"job_id":"<sha256>","line_id":"example-001","speaker_id":"npc-001","speaker":"示例侠客","runtime_speaker":"示例侠客","text":"前路尚远，我们先在此歇息片刻。","tts_text":"前路尚远，我们先在此歇息片刻。","tts_text_strategy":"dialogue_text","voice_group_id":"0123456789abcdef","audio_file":"offline/audio/ab/<sha256>.wav","runtime_key":"<sha256>","runtime_speaker_aliases":["示例侠客"],"source_occurrences":1}
```

## dialogue event（JSONL）

UE4SS 事件桥输出：

```json
{"time":"2026-01-01T00:00:00Z","event":"dialogue","speaker":"示例侠客","text":"前路尚远，我们先在此歇息片刻。","source":"dialogue_widget"}
```

## compact runtime lookup

```json
{
  "version": 2,
  "algorithm": "sha256(normalized_speaker\\0normalized_text)",
  "exact": {
    "<sha256>": "offline/audio/ab/<sha256>.wav"
  },
  "text_fallback": {},
  "prefix": []
}
```

只按文本建立兜底索引时，必须确认候选只指向一个音频。冲突文本应留在碰撞报告，
不能任意选择一个角色。
