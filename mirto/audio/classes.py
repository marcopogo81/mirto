# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Tipi di suono che Mirto sa riconoscere, ricavati dalle 521 classi di YAMNet (AudioSet).

Ogni tipo raggruppa più classi AudioSet; il punteggio del tipo è il massimo delle sue classi.
Le chiavi finiscono in configurazione e negli obiettivi delle azioni come ``sound:<tipo>``.
"""

from __future__ import annotations

from ..i18n import N_, _

SOUND_PREFIX = "sound:"

# tipo -> (nome, emoji, classi AudioSet, soglia predefinita, frame di conferma)
SOUNDS: dict[str, tuple[str, str, list[str], float, int]] = {
    "glass": (N_("Vetro rotto"), "🪟", ["Glass", "Shatter"], 0.30, 1),
    "smoke_alarm": (N_("Allarme fumo o antincendio"), "🔥", ["Smoke detector, smoke alarm", "Fire alarm"], 0.30, 2),
    "alarm": (N_("Sirena o allarme"), "🚨", ["Alarm", "Siren", "Civil defense siren", "Car alarm", "Buzzer"], 0.35, 2),
    "scream": (N_("Urla"), "😱", ["Screaming", "Shout", "Yell", "Children shouting"], 0.40, 2),
    "crying": (N_("Pianto"), "😢", ["Crying, sobbing", "Baby cry, infant cry", "Whimper"], 0.40, 2),
    "gunshot": (N_("Spari o esplosioni"), "💥", ["Gunshot, gunfire", "Machine gun", "Explosion", "Fireworks",
                                               "Firecracker", "Cap gun"], 0.40, 1),
    "dog": (N_("Cane che abbaia"), "🐕", ["Dog", "Bark", "Bow-wow", "Growling", "Howl"], 0.40, 2),
    "knock": (N_("Colpi o porta sbattuta"), "🚪", ["Knock", "Slam", "Doorbell", "Ding-dong"], 0.40, 1),
    "speech": (N_("Voci"), "🗣", ["Speech", "Conversation", "Whispering"], 0.50, 3),
    "bird": (N_("Versi di uccelli"), "🐦", ["Bird", "Bird vocalization, bird call, bird song", "Crow", "Caw",
                                          "Pigeon, dove", "Coo", "Squawk"], 0.40, 2),
}


def sound_key(kind: str) -> str:
    return SOUND_PREFIX + kind


def is_sound(key: str) -> bool:
    return key.startswith(SOUND_PREFIX)


def sound_name(key: str) -> str:
    kind = key[len(SOUND_PREFIX):] if is_sound(key) else key
    return _(SOUNDS[kind][0]) if kind in SOUNDS else kind


def sound_emoji(key: str) -> str:
    kind = key[len(SOUND_PREFIX):] if is_sound(key) else key
    return SOUNDS[kind][1] if kind in SOUNDS else "🔊"
