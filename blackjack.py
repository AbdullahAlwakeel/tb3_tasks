"""Command-line blackjack game that plays exactly 50 rounds.

Run interactively:
    python3 blackjack.py

Run without prompts:
    python3 blackjack.py --auto
"""

from __future__ import annotations

import argparse
import os
import random
import time
from dataclasses import dataclass
from typing import Iterable


ROUNDS_TO_PLAY = 50
DEFAULT_TRACE_FILE = "blackjack_trace.txt"


SUITS = ("hearts", "diamonds", "clubs", "spades")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
CARD_VALUES = {
    "A": 11,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
}


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    def __str__(self) -> str:
        return f"{self.rank} of {self.suit}"


def seed_random_once_at_startup() -> int:
    """Seed Python's random module once with fresh entropy for this run."""
    entropy = int.from_bytes(os.urandom(32), "big")
    seed = entropy ^ time.time_ns()
    random.seed(seed)
    return seed


def new_shuffled_deck() -> list[Card]:
    deck = [Card(rank, suit) for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def hand_value(hand: Iterable[Card]) -> int:
    total = sum(CARD_VALUES[card.rank] for card in hand)
    aces = sum(1 for card in hand if card.rank == "A")

    while total > 21 and aces:
        total -= 10
        aces -= 1

    return total


def format_hand(hand: list[Card]) -> str:
    return ", ".join(str(card) for card in hand)


def draw(deck: list[Card]) -> Card:
    if not deck:
        deck.extend(new_shuffled_deck())
    return deck.pop()


def wants_hit_interactive(round_number: int, player_hand: list[Card], dealer_up_card: Card) -> bool:
    while True:
        print()
        print(f"Round {round_number}")
        print(f"Dealer shows: {dealer_up_card}")
        print(f"Your hand: {format_hand(player_hand)} ({hand_value(player_hand)})")
        choice = input("Hit or stand? [h/s]: ").strip().lower()

        if choice in {"h", "hit"}:
            return True
        if choice in {"s", "stand"}:
            return False

        print("Please enter 'h' to hit or 's' to stand.")


def wants_hit_auto(player_hand: list[Card], dealer_up_card: Card) -> bool:
    player_total = hand_value(player_hand)
    dealer_value = CARD_VALUES[dealer_up_card.rank]

    if player_total <= 11:
        return True
    if player_total == 12:
        return dealer_value in {2, 3, 7, 8, 9, 10, 11}
    if 13 <= player_total <= 16:
        return dealer_value >= 7
    return False


def play_round(round_number: int, deck: list[Card], automatic: bool) -> tuple[bool, list[str]]:
    trace: list[str] = [f"Round {round_number}"]
    player_hand = [draw(deck), draw(deck)]
    dealer_hand = [draw(deck), draw(deck)]

    trace.append(f"Player starting hand: {format_hand(player_hand)} ({hand_value(player_hand)})")
    trace.append(f"Dealer starting hand: {format_hand(dealer_hand)} ({hand_value(dealer_hand)})")

    while hand_value(player_hand) < 21:
        if automatic:
            wants_hit = wants_hit_auto(player_hand, dealer_hand[0])
        else:
            wants_hit = wants_hit_interactive(round_number, player_hand, dealer_hand[0])

        if not wants_hit:
            trace.append(f"Player stands at {hand_value(player_hand)}")
            break

        card = draw(deck)
        player_hand.append(card)
        trace.append(f"Player hits and draws {card}; total is {hand_value(player_hand)}")

    player_total = hand_value(player_hand)
    if player_total > 21:
        trace.append(f"Player busts at {player_total}")
        trace.append("Result: dealer was not beaten")
        return False, trace

    if player_total == 21:
        trace.append("Player has 21")

    while hand_value(dealer_hand) < 17:
        card = draw(deck)
        dealer_hand.append(card)
        trace.append(f"Dealer hits and draws {card}; total is {hand_value(dealer_hand)}")

    dealer_total = hand_value(dealer_hand)
    if dealer_total > 21:
        trace.append(f"Dealer busts at {dealer_total}")
        trace.append("Result: player beat the dealer")
        return True, trace

    trace.append(f"Final player total: {player_total}")
    trace.append(f"Final dealer total: {dealer_total}")

    if player_total > dealer_total:
        trace.append("Result: player beat the dealer")
        return True, trace

    if player_total == dealer_total:
        trace.append("Result: push; dealer was not beaten")
        return False, trace

    trace.append("Result: dealer was not beaten")
    return False, trace


def write_trace_header(trace_file, seed: int, automatic: bool) -> None:
    mode = "automatic" if automatic else "interactive"
    trace_file.write("Blackjack trace\n")
    trace_file.write(f"Mode: {mode}\n")
    trace_file.write(f"Random seed set once at startup: {seed}\n")
    trace_file.write(f"Rounds scheduled: {ROUNDS_TO_PLAY}\n")
    trace_file.write("\n")


def run_game(trace_path: str, automatic: bool, seed: int) -> int:
    deck = new_shuffled_deck()
    dealer_beaten_count = 0

    with open(trace_path, "w", encoding="utf-8") as trace_file:
        write_trace_header(trace_file, seed, automatic)

        for round_number in range(1, ROUNDS_TO_PLAY + 1):
            dealer_was_beaten, round_trace = play_round(round_number, deck, automatic)
            if dealer_was_beaten:
                dealer_beaten_count += 1

            for line in round_trace:
                trace_file.write(line + "\n")
            trace_file.write(f"Dealer beaten count so far: {dealer_beaten_count}\n")
            trace_file.write("\n")

            print(
                f"Round {round_number:02d}/{ROUNDS_TO_PLAY}: "
                f"dealer beaten {dealer_beaten_count} time(s)"
            )

        trace_file.write(f"Final dealer beaten count: {dealer_beaten_count}\n")

    return dealer_beaten_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play exactly 50 rounds of command-line blackjack."
    )
    parser.add_argument(
        "--trace-file",
        default=DEFAULT_TRACE_FILE,
        help=f"path to the text trace output file (default: {DEFAULT_TRACE_FILE})",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="play all 50 rounds automatically instead of prompting for hit/stand",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seed = seed_random_once_at_startup()
    dealer_beaten_count = run_game(args.trace_file, args.auto, seed)

    print()
    print(f"Finished {ROUNDS_TO_PLAY} rounds.")
    print(f"Player beat the dealer {dealer_beaten_count} time(s).")
    print(f"Trace written to {args.trace_file}")


if __name__ == "__main__":
    main()
