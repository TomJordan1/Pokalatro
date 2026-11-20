// Fast 5-7 card evaluator and random-hand Monte Carlo for PokerFight simulator
// (benchmarking/training tool only; production bot is 100% pure Python).
#include <cstdint>
#include <cstring>
#include <algorithm>

static int STRAIGHT[8192];
static int TOP5[8192];
static bool INITED = false;

static void init_tables() {
    if (INITED) return;
    for (int m = 0; m < 8192; ++m) {
        int st = -1;
        for (int high = 12; high >= 4; --high) {
            int win = 0x1F << (high - 4);
            if ((m & win) == win) {
                st = high;
                break;
            }
        }
        if (st < 0 && (m & 0x100F) == 0x100F) {
            st = 3; // A-2-3-4-5 wheel
        }
        STRAIGHT[m] = st;
        int val = 0, n = 0;
        for (int r = 12; r >= 0; --r) {
            if ((m >> r) & 1) {
                val = val * 16 + r;
                if (++n == 5) break;
            }
        }
        TOP5[m] = val;
    }
    INITED = true;
}

static inline int top_n(int mask, int n) {
    int val = 0;
    for (int r = 12; r >= 0 && n > 0; --r) {
        if ((mask >> r) & 1) {
            val = val * 16 + r;
            --n;
        }
    }
    while (n-- > 0) val *= 16;
    return val;
}

extern "C" {

int eval_cards(const int* cards, int n) {
    if (!INITED) init_tables();
    int cnt[13] = {0};
    int sc[4] = {0};
    int sm[4] = {0};
    int mask = 0;
    for (int i = 0; i < n; ++i) {
        int c = cards[i];
        int r = c >> 2;
        int s = c & 3;
        cnt[r]++;
        sc[s]++;
        sm[s] |= (1 << r);
        mask |= (1 << r);
    }
    for (int s = 0; s < 4; ++s) {
        if (sc[s] >= 5) {
            int m = sm[s];
            int st = STRAIGHT[m];
            if (st >= 0) return (8 << 20) | st;
            return (5 << 20) | TOP5[m];
        }
    }
    int four = -1;
    int t0 = -1, t1 = -1;
    int p0 = -1, p1 = -1;
    for (int r = 12; r >= 0; --r) {
        int k = cnt[r];
        if (k == 4) four = r;
        else if (k == 3) {
            if (t0 < 0) t0 = r;
            else if (t1 < 0) t1 = r;
        } else if (k == 2) {
            if (p0 < 0) p0 = r;
            else if (p1 < 0) p1 = r;
        }
    }
    if (four >= 0) {
        return (7 << 20) | (four << 4) | top_n(mask & ~(1 << four), 1);
    }
    if (t0 >= 0 && (t1 >= 0 || p0 >= 0)) {
        int p = (t1 >= 0 && (p0 < 0 || t1 > p0)) ? t1 : p0;
        return (6 << 20) | (t0 << 4) | p;
    }
    int st = STRAIGHT[mask];
    if (st >= 0) return (4 << 20) | st;
    if (t0 >= 0) return (3 << 20) | (t0 << 8) | top_n(mask & ~(1 << t0), 2);
    if (p1 >= 0) {
        int rem = mask & ~(1 << p0) & ~(1 << p1);
        return (2 << 20) | (p0 << 8) | (p1 << 4) | top_n(rem, 1);
    }
    if (p0 >= 0) return (1 << 20) | (p0 << 12) | top_n(mask & ~(1 << p0), 3);
    return TOP5[mask];
}

static inline uint64_t splitmix64(uint64_t& x) {
    uint64_t z = (x += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

double mc_equity_random(int c0, int c1, const int* board, int nb, int n_rivals, int samples, uint64_t seed) {
    if (!INITED) init_tables();
    if (n_rivals <= 0) return 1.0;
    bool used[52] = {false};
    used[c0] = true;
    used[c1] = true;
    int tab[7];
    for (int i = 0; i < nb; ++i) {
        used[board[i]] = true;
        tab[2 + i] = board[i];
    }
    int deck[52];
    int dn = 0;
    for (int c = 0; c < 52; ++c) {
        if (!used[c]) deck[dn++] = c;
    }
    int missing = 5 - nb;
    int need = missing + 2 * n_rivals;
    if (need > dn || samples <= 0) return 0.5;

    uint64_t rng = seed ? seed : 0x123456789abcdef0ULL;
    double total = 0.0;
    for (int s = 0; s < samples; ++s) {
        for (int i = 0; i < need; ++i) {
            int j = i + (int)(splitmix64(rng) % (uint64_t)(dn - i));
            int tmp = deck[i]; deck[i] = deck[j]; deck[j] = tmp;
        }
        for (int k = 0; k < missing; ++k) {
            tab[2 + nb + k] = deck[k];
        }
        tab[0] = c0; tab[1] = c1;
        int my_score = eval_cards(tab, 7);
        int ties = 1;
        bool lost = false;
        for (int r = 0; r < n_rivals; ++r) {
            tab[0] = deck[missing + 2 * r];
            tab[1] = deck[missing + 2 * r + 1];
            int sc = eval_cards(tab, 7);
            if (sc > my_score) {
                lost = true;
                break;
            }
            if (sc == my_score) ties++;
        }
        if (!lost) total += 1.0 / (double)ties;
    }
    return total / (double)samples;
}

}
