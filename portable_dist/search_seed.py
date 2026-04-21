from __future__ import annotations

from functools import lru_cache
from multiprocessing import Pool, cpu_count
import random
import time

SUITS = ("hearts", "diamonds", "clubs", "spades")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
VALUES = {
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


def hand_value(hand: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> int:
    total = 0
    aces = 0
    for rank, _suit in hand:
        total += VALUES[rank]
        aces += rank == "A"
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def build_draws(seed: int, decks: int = 20) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    draws: list[tuple[str, str]] = []
    for _ in range(decks):
        deck = [(rank, suit) for suit in SUITS for rank in RANKS]
        rng.shuffle(deck)
        draws.extend(reversed(deck))
    return draws


def score_seed(seed: int) -> int:
    draws = build_draws(seed)

    @lru_cache(None)
    def dp(round_number: int, pos: int) -> int:
        if round_number > 50:
            return 0

        player_start = (draws[pos], draws[pos + 1])
        dealer_start = (draws[pos + 2], draws[pos + 3])
        after_deal = pos + 4
        best = -1

        def settle(player_hand: tuple[tuple[str, str], ...], next_pos: int) -> None:
            nonlocal best
            player_total = hand_value(player_hand)
            if player_total > 21:
                value = dp(round_number + 1, next_pos)
            else:
                dealer_hand = [dealer_start[0], dealer_start[1]]
                while hand_value(dealer_hand) < 17:
                    dealer_hand.append(draws[next_pos])
                    next_pos += 1
                dealer_total = hand_value(dealer_hand)
                win = int(dealer_total > 21 or player_total > dealer_total)
                value = win + dp(round_number + 1, next_pos)
            best = max(best, value)

        def choose(player_hand: tuple[tuple[str, str], ...], next_pos: int) -> None:
            if hand_value(player_hand) >= 21:
                settle(player_hand, next_pos)
                return
            settle(player_hand, next_pos)
            choose(player_hand + (draws[next_pos],), next_pos + 1)

        choose(player_start, after_deal)
        return best

    return dp(1, 0)


def search_chunk(args: tuple[int, int]) -> tuple[str, int, int]:
    start, count = args
    best_score = -1
    best_seed = start
    for seed in range(start, start + count):
        score = score_seed(seed)
        if score > best_score:
            best_score = score
            best_seed = seed
        if score >= 47:
            return ("FOUND", seed, score)
    return ("BEST", best_seed, best_score)


def main() -> None:
    workers = min(8, cpu_count() or 2)
    batch = 500
    limit = 500_000
    started = time.time()
    best = (0, -1)
    print(f"workers={workers} limit={limit}", flush=True)
    with Pool(workers) as pool:
        chunks = ((start, batch) for start in range(0, limit, batch))
        for kind, seed, score in pool.imap_unordered(search_chunk, chunks):
            if score > best[0]:
                best = (score, seed)
                print(f"best score={score} seed={seed} elapsed={time.time() - started:.1f}s", flush=True)
            if kind == "FOUND":
                print(f"FOUND seed={seed} score={score}", flush=True)
                pool.terminate()
                return
    print(f"DONE best_score={best[0]} best_seed={best[1]}", flush=True)


if __name__ == "__main__":
    main()
