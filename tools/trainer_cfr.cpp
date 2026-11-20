// Offline External-Sampling MCCFR / CFR+ Blueprint Trainer for Pokalatro v9.
// Inspired by ericgjackson/slumbot2019 (ecfr.cpp / cfrp.cpp) and Pluribus (Brown & Sandholm 2019).

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <numeric>
#include <random>
#include <string>
#include <vector>

static int STRAIGHT[8192];
static int TOP5[8192];

static void init_eval() {
    for (int m = 0; m < 8192; ++m) {
        int st = -1;
        for (int high = 12; high >= 4; --high) {
            int win = 0x1F << (high - 4);
            if ((m & win) == win) { st = high; break; }
        }
        if (st < 0 && (m & 0x100F) == 0x100F) st = 3;
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
}

static inline int top_n(int mask, int n) {
    int val = 0;
    for (int r = 12; r >= 0 && n > 0; --r) {
        if ((mask >> r) & 1) { val = val * 16 + r; --n; }
    }
    while (n-- > 0) val *= 16;
    return val;
}

static int eval_cards(const int* cards, int n) {
    int cnt[13] = {0}, sc[4] = {0}, sm[4] = {0}, mask = 0;
    for (int i = 0; i < n; ++i) {
        int c = cards[i], r = c >> 2, s = c & 3;
        cnt[r]++; sc[s]++; sm[s] |= (1 << r); mask |= (1 << r);
    }
    for (int s = 0; s < 4; ++s) {
        if (sc[s] >= 5) {
            int m = sm[s], st = STRAIGHT[m];
            return st >= 0 ? ((8 << 20) | st) : ((5 << 20) | TOP5[m]);
        }
    }
    int four = -1, t0 = -1, t1 = -1, p0 = -1, p1 = -1;
    for (int r = 12; r >= 0; --r) {
        int k = cnt[r];
        if (k == 4) four = r;
        else if (k == 3) { if (t0 < 0) t0 = r; else if (t1 < 0) t1 = r; }
        else if (k == 2) { if (p0 < 0) p0 = r; else if (p1 < 0) p1 = r; }
    }
    if (four >= 0) return (7 << 20) | (four << 4) | top_n(mask & ~(1 << four), 1);
    if (t0 >= 0 && (t1 >= 0 || p0 >= 0)) {
        int p = (t1 >= 0 && (p0 < 0 || t1 > p0)) ? t1 : p0;
        return (6 << 20) | (t0 << 4) | p;
    }
    int st = STRAIGHT[mask];
    if (st >= 0) return (4 << 20) | st;
    if (t0 >= 0) return (3 << 20) | (t0 << 8) | top_n(mask & ~(1 << t0), 2);
    if (p1 >= 0) return (2 << 20) | (p0 << 8) | (p1 << 4) | top_n(mask & ~(1 << p0) & ~(1 << p1), 1);
    if (p0 >= 0) return (1 << 20) | (p0 << 12) | top_n(mask & ~(1 << p0), 3);
    return TOP5[mask];
}

static inline uint64_t splitmix64(uint64_t& x) {
    uint64_t z = (x += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

struct HandClass {
    int hi, lo;
    bool suited;
    double score;
};

static std::vector<HandClass> CLASSES;
static double EQ169[169][169];
static double COMBO_WEIGHT[169];

static double mc_1v1(int c0, int c1, int samples, uint64_t& rng) {
    int deck[50], dn = 0;
    for (int c = 0; c < 52; ++c) if (c != c0 && c != c1) deck[dn++] = c;
    double w = 0.0;
    int tab[7];
    for (int s = 0; s < samples; ++s) {
        for (int i = 0; i < 7; ++i) {
            int j = i + (int)(splitmix64(rng) % (uint64_t)(dn - i));
            std::swap(deck[i], deck[j]);
        }
        for (int k = 0; k < 5; ++k) tab[2 + k] = deck[k];
        tab[0] = c0; tab[1] = c1;
        int ms = eval_cards(tab, 7);
        tab[0] = deck[5]; tab[1] = deck[6];
        int os = eval_cards(tab, 7);
        if (ms > os) w += 1.0;
        else if (ms == os) w += 0.5;
    }
    return w / samples;
}

static double mc_4way(int c0, int c1, int samples, uint64_t& rng) {
    int deck[50], dn = 0;
    for (int c = 0; c < 52; ++c) if (c != c0 && c != c1) deck[dn++] = c;
    double w = 0.0;
    int tab[7];
    for (int s = 0; s < samples; ++s) {
        for (int i = 0; i < 13; ++i) {
            int j = i + (int)(splitmix64(rng) % (uint64_t)(dn - i));
            std::swap(deck[i], deck[j]);
        }
        for (int k = 0; k < 5; ++k) tab[2 + k] = deck[k];
        tab[0] = c0; tab[1] = c1;
        int ms = eval_cards(tab, 7);
        int ties = 1; bool lost = false;
        for (int r = 0; r < 4; ++r) {
            tab[0] = deck[5 + 2 * r]; tab[1] = deck[6 + 2 * r];
            int os = eval_cards(tab, 7);
            if (os > ms) { lost = true; break; }
            if (os == ms) ties++;
        }
        if (!lost) w += 1.0 / ties;
    }
    return w / samples;
}

static void build_classes(uint64_t& rng) {
    CLASSES.clear();
    for (int hi = 14; hi >= 2; --hi) {
        for (int lo = hi; lo >= 2; --lo) {
            if (hi == lo) {
                int c0 = (hi - 2) * 4 + 0, c1 = (lo - 2) * 4 + 1;
                double eq1 = mc_1v1(c0, c1, 8000, rng);
                double eq4 = mc_4way(c0, c1, 4000, rng);
                double b = (hi >= 12) ? 0.025 : (hi <= 8 ? -0.022 : 0.0);
                CLASSES.push_back({hi, lo, false, 0.55 * eq1 + 0.30 * (eq4 * 1.50) + b});
            } else {
                for (int mp = 1; mp >= 0; --mp) {
                    int c0 = (hi - 2) * 4 + 0;
                    int c1 = (lo - 2) * 4 + (mp ? 0 : 1);
                    double eq1 = mc_1v1(c0, c1, 8000, rng);
                    double eq4 = mc_4way(c0, c1, 4000, rng);
                    double b = mp ? 0.022 : 0.0;
                    int gap = hi - lo;
                    if (gap == 1) b += 0.018;
                    else if (gap == 2) b += 0.010;
                    else if (gap == 3) b += 0.003;
                    if (hi >= 13 && lo >= 11) b += 0.025;
                    CLASSES.push_back({hi, lo, mp == 1, 0.55 * eq1 + 0.30 * (eq4 * 1.50) + b});
                }
            }
        }
    }
    std::sort(CLASSES.begin(), CLASSES.end(), [](const HandClass& a, const HandClass& b) {
        return a.score > b.score;
    });
}

static void compute_eq169(uint64_t& rng) {
    for (int i = 0; i < 169; ++i) {
        COMBO_WEIGHT[i] = (CLASSES[i].hi == CLASSES[i].lo) ? 6.0 : (CLASSES[i].suited ? 4.0 : 12.0);
    }
    for (int i = 0; i < 169; ++i) {
        EQ169[i][i] = 0.5;
        int a0 = (CLASSES[i].hi - 2) * 4 + 0;
        int a1 = (CLASSES[i].lo - 2) * 4 + (CLASSES[i].suited ? 0 : 1);
        for (int j = i + 1; j < 169; ++j) {
            int b0 = (CLASSES[j].hi - 2) * 4 + 2;
            int b1 = (CLASSES[j].lo - 2) * 4 + (CLASSES[j].suited ? 2 : 3);
            if (b0 == a0 || b0 == a1) b0 = (CLASSES[j].hi - 2) * 4 + 1;
            if (b1 == a0 || b1 == a1 || b1 == b0) b1 = (CLASSES[j].lo - 2) * 4 + 0;
            if (b1 == a0 || b1 == a1 || b1 == b0) b1 = (CLASSES[j].lo - 2) * 4 + 2;

            int deck[48], dn = 0;
            for (int c = 0; c < 52; ++c) {
                if (c != a0 && c != a1 && c != b0 && c != b1) deck[dn++] = c;
            }
            double w = 0.0;
            const int S = 200;
            int tab[7];
            for (int s = 0; s < S; ++s) {
                for (int k = 0; k < 5; ++k) {
                    int p = k + (int)(splitmix64(rng) % (uint64_t)(dn - k));
                    std::swap(deck[k], deck[p]);
                    tab[2 + k] = deck[k];
                }
                tab[0] = a0; tab[1] = a1;
                int sa = eval_cards(tab, 7);
                tab[0] = b0; tab[1] = b1;
                int sb = eval_cards(tab, 7);
                if (sa > sb) w += 1.0;
                else if (sa == sb) w += 0.5;
            }
            double e = w / S;
            EQ169[i][j] = e;
            EQ169[j][i] = 1.0 - e;
        }
    }
}

struct PushFoldSolution {
    std::array<double, 169> push_prob;
    std::array<double, 169> call_prob;
};

static PushFoldSolution solve_push_fold_cfrp(double stack_bb, int callers_behind, int iters) {
    // Wealth measured relative to start of hand (after blinds posted: -0.5 for SB, -1.0 for BB, 0 for others).
    // If HU (callers_behind == 1): Opener is SB (-0.5 already posted), Caller is BB (-1.0 already posted).
    //   Opener fold -> 0.0 delta from current state (-0.5 total); Opener wins uncalled -> +1.5 (net +1.0).
    //   Wait: let's measure ALL payoffs from start of hand (before posting blinds):
    //   HU: fold -> -0.5; uncalled push -> +1.0; called -> eq * (2*stack_bb) - stack_bb.
    //   MW (callers_behind >= 2): Opener not in blinds (dead=1.5 in blinds, caller puts stack_bb total):
    //     fold -> 0.0; uncalled push -> +1.5; called -> eq * (2*stack_bb + 0.5) - stack_bb.
    std::vector<double> reg_push(169 * 2, 0.0), sum_push(169 * 2, 0.0);
    std::vector<double> reg_call(169 * 2, 0.0), sum_call(169 * 2, 0.0);
    std::array<double, 169> sp{}, sc{};

    double total_w = 0.0;
    for (int i = 0; i < 169; ++i) total_w += COMBO_WEIGHT[i];

    bool is_hu = (callers_behind == 1);
    double ev_opener_fold = is_hu ? -0.5 : 0.0;
    double ev_opener_uncalled = is_hu ? 1.0 : 1.5;
    double pot_called = is_hu ? (2.0 * stack_bb) : (2.0 * stack_bb + 0.5);
    double ev_caller_fold = -1.0;

    for (int t = 1; t <= iters; ++t) {
        for (int i = 0; i < 169; ++i) {
            double r0 = std::max(0.0, reg_push[2 * i + 0]);
            double r1 = std::max(0.0, reg_push[2 * i + 1]);
            double rs = r0 + r1;
            sp[i] = rs > 0 ? r1 / rs : 0.5;

            double c0 = std::max(0.0, reg_call[2 * i + 0]);
            double c1 = std::max(0.0, reg_call[2 * i + 1]);
            double cs = c0 + c1;
            sc[i] = cs > 0 ? c1 / cs : 0.5;
        }

        double p_call_single = 0.0;
        for (int j = 0; j < 169; ++j) p_call_single += COMBO_WEIGHT[j] * sc[j];
        p_call_single /= total_w;
        double p_any_call = 1.0 - std::pow(1.0 - p_call_single, (double)callers_behind);

        for (int i = 0; i < 169; ++i) {
            double ev_win_vs_call = 0.0;
            for (int j = 0; j < 169; ++j) {
                ev_win_vs_call += COMBO_WEIGHT[j] * sc[j] * EQ169[i][j];
            }
            double avg_eq = (p_call_single > 1e-9) ? (ev_win_vs_call / (total_w * p_call_single)) : 0.5;
            double mw_eq = std::pow(avg_eq, 1.0 + 0.14 * std::max(0, callers_behind - 1));
            double ev_called = mw_eq * pot_called - stack_bb;
            double ev_push = (1.0 - p_any_call) * ev_opener_uncalled + p_any_call * ev_called;
            double ev_cur = (1.0 - sp[i]) * ev_opener_fold + sp[i] * ev_push;

            reg_push[2 * i + 0] = std::max(0.0, reg_push[2 * i + 0] + (ev_opener_fold - ev_cur));
            reg_push[2 * i + 1] = std::max(0.0, reg_push[2 * i + 1] + (ev_push - ev_cur));
            sum_push[2 * i + 0] += t * (1.0 - sp[i]);
            sum_push[2 * i + 1] += t * sp[i];
        }

        double p_push = 0.0;
        for (int i = 0; i < 169; ++i) p_push += COMBO_WEIGHT[i] * sp[i];
        p_push /= total_w;
        if (p_push > 1e-9) {
            for (int j = 0; j < 169; ++j) {
                double eq_vs_push = 0.0;
                for (int i = 0; i < 169; ++i) {
                    eq_vs_push += COMBO_WEIGHT[i] * sp[i] * EQ169[j][i];
                }
                eq_vs_push /= (total_w * p_push);
                double ev_call = eq_vs_push * pot_called - stack_bb;
                double ev_cur = (1.0 - sc[j]) * ev_caller_fold + sc[j] * ev_call;

                reg_call[2 * j + 0] = std::max(0.0, reg_call[2 * j + 0] + (ev_caller_fold - ev_cur));
                reg_call[2 * j + 1] = std::max(0.0, reg_call[2 * j + 1] + (ev_call - ev_cur));
                sum_call[2 * j + 0] += t * (1.0 - sc[j]);
                sum_call[2 * j + 1] += t * sc[j];
            }
        }
    }

    PushFoldSolution sol;
    for (int i = 0; i < 169; ++i) {
        double ps = sum_push[2 * i + 0] + sum_push[2 * i + 1];
        sol.push_prob[i] = ps > 0 ? sum_push[2 * i + 1] / ps : 0.0;
        double cs = sum_call[2 * i + 0] + sum_call[2 * i + 1];
        sol.call_prob[i] = cs > 0 ? sum_call[2 * i + 1] / cs : 0.0;
    }
    return sol;
}

struct DeepPreflopSolution {
    std::array<double, 169> open_prob;
    std::array<double, 169> vs_open_call;
    std::array<double, 169> vs_open_3bet;
    std::array<double, 169> vs_3bet_call;
    std::array<double, 169> vs_3bet_4bet;
};

static DeepPreflopSolution solve_deep_preflop_cfrp(int behind, double eff_stack_bb, int iters) {
    std::vector<std::array<double, 2>> r0(169), s0(169);
    std::vector<std::array<double, 3>> r1(169), s1(169);
    std::vector<std::array<double, 3>> r2(169), s2(169);
    std::vector<std::array<double, 2>> r3(169), s3(169);

    double total_w = 0.0;
    for (int i = 0; i < 169; ++i) total_w += COMBO_WEIGHT[i];

    double real_open_called = (behind <= 1) ? 1.02 : 0.88;
    double real_def_called = (behind <= 1) ? 0.86 : 0.96;
    double open_sz = 2.8;
    double three_bet_sz = 9.0;
    double four_bet_sz = std::min(eff_stack_bb, 26.0);
    double dead = 1.5;

    for (int t = 1; t <= iters; ++t) {
        std::array<std::array<double, 2>, 169> p0{}, p3{};
        std::array<std::array<double, 3>, 169> p1{}, p2{};

        for (int i = 0; i < 169; ++i) {
            auto rm2 = [](const std::array<double, 2>& r) {
                double a = std::max(0.0, r[0]), b = std::max(0.0, r[1]), s = a + b;
                return s > 0 ? std::array<double, 2>{a / s, b / s} : std::array<double, 2>{0.5, 0.5};
            };
            auto rm3 = [](const std::array<double, 3>& r) {
                double a = std::max(0.0, r[0]), b = std::max(0.0, r[1]), c = std::max(0.0, r[2]), s = a + b + c;
                return s > 0 ? std::array<double, 3>{a / s, b / s, c / s} : std::array<double, 3>{0.333, 0.333, 0.334};
            };
            p0[i] = rm2(r0[i]);
            p1[i] = rm3(r1[i]);
            p2[i] = rm3(r2[i]);
            p3[i] = rm2(r3[i]);
        }

        for (int i = 0; i < 169; ++i) {
            double ev_3bet_fold = -open_sz;
            double ev_3bet_call = 0.0, ev_3bet_4bet = 0.0, w_3bet = 0.0;
            double ev_open_call = 0.0, w_call = 0.0, w_fold = 0.0;

            for (int j = 0; j < 169; ++j) {
                double wj = COMBO_WEIGHT[j];
                double eq = EQ169[i][j];
                w_fold += wj * p1[j][0];
                w_call += wj * p1[j][1];
                w_3bet += wj * p1[j][2];

                ev_open_call += wj * p1[j][1] * (eq * real_open_called * (2 * open_sz + 0.5) - open_sz);
                ev_3bet_call += wj * p1[j][2] * (eq * 0.94 * (2 * three_bet_sz + 0.5) - three_bet_sz);

                double ev_vs_4bet_fold = three_bet_sz + 0.5;
                double ev_vs_4bet_call = eq * (2 * four_bet_sz + 0.5) - four_bet_sz;
                ev_3bet_4bet += wj * p1[j][2] * (p3[j][0] * ev_vs_4bet_fold + p3[j][1] * ev_vs_4bet_call);
            }
            double e2_0 = ev_3bet_fold;
            double e2_1 = w_3bet > 1e-9 ? ev_3bet_call / w_3bet : 0.0;
            double e2_2 = w_3bet > 1e-9 ? ev_3bet_4bet / w_3bet : 0.0;
            double e2_cur = p2[i][0] * e2_0 + p2[i][1] * e2_1 + p2[i][2] * e2_2;
            r2[i][0] = std::max(0.0, r2[i][0] + (e2_0 - e2_cur));
            r2[i][1] = std::max(0.0, r2[i][1] + (e2_1 - e2_cur));
            r2[i][2] = std::max(0.0, r2[i][2] + (e2_2 - e2_cur));
            for (int a = 0; a < 3; ++a) s2[i][a] += t * p2[i][a];

            // With `behind` players remaining, probability all fold scales as (w_fold/total_w)^behind
            double pf_single = w_fold / total_w;
            double pf = std::pow(pf_single, (double)behind);
            double p_act = 1.0 - pf;
            double p3_share = (w_call + w_3bet > 1e-9) ? (w_3bet / (w_call + w_3bet)) : 0.25;
            double p3b = p_act * std::min(0.85, p3_share * (1.0 + 0.15 * (behind - 1)));
            double pc = std::max(0.0, p_act - p3b);
            double e_open = pf * dead + pc * (w_call > 1e-9 ? ev_open_call / w_call : 0.0) + p3b * e2_cur;
            double e0_cur = p0[i][1] * e_open;
            r0[i][0] = std::max(0.0, r0[i][0] + (0.0 - e0_cur));
            r0[i][1] = std::max(0.0, r0[i][1] + (e_open - e0_cur));
            s0[i][0] += t * p0[i][0];
            s0[i][1] += t * p0[i][1];
        }

        double w_open = 0.0;
        for (int i = 0; i < 169; ++i) w_open += COMBO_WEIGHT[i] * p0[i][1];
        if (w_open > 1e-9) {
            for (int j = 0; j < 169; ++j) {
                double ev_call_open = 0.0, ev_3bet = 0.0;
                double w_4bet = 0.0, ev_4bet_call = 0.0;
                for (int i = 0; i < 169; ++i) {
                    double wi = COMBO_WEIGHT[i] * p0[i][1];
                    double eq = EQ169[j][i];
                    ev_call_open += wi * (eq * real_def_called * (2 * open_sz + 0.5) - (open_sz - 1.0));
                    double e_op_f = open_sz + 0.5;
                    double e_op_c = eq * 0.96 * (2 * three_bet_sz + 0.5) - (three_bet_sz - 1.0);
                    double e_op_4 = -(three_bet_sz - 1.0);
                    w_4bet += wi * p2[i][2];
                    ev_4bet_call += wi * p2[i][2] * (eq * (2 * four_bet_sz + 0.5) - (four_bet_sz - 1.0));
                    double e3_best = p3[j][0] * e_op_4 + p3[j][1] * (eq * (2 * four_bet_sz + 0.5) - (four_bet_sz - 1.0));
                    ev_3bet += wi * (p2[i][0] * e_op_f + p2[i][1] * e_op_c + p2[i][2] * e3_best);
                }
                if (w_4bet > 1e-9) {
                    double e3_0 = -(three_bet_sz - 1.0);
                    double e3_1 = ev_4bet_call / w_4bet;
                    double e3_cur = p3[j][0] * e3_0 + p3[j][1] * e3_1;
                    r3[j][0] = std::max(0.0, r3[j][0] + (e3_0 - e3_cur));
                    r3[j][1] = std::max(0.0, r3[j][1] + (e3_1 - e3_cur));
                    s3[j][0] += t * p3[j][0];
                    s3[j][1] += t * p3[j][1];
                }
                double e1_0 = 0.0;
                double e1_1 = ev_call_open / w_open;
                double e1_2 = ev_3bet / w_open;
                double e1_cur = p1[j][0] * e1_0 + p1[j][1] * e1_1 + p1[j][2] * e1_2;
                r1[j][0] = std::max(0.0, r1[j][0] + (e1_0 - e1_cur));
                r1[j][1] = std::max(0.0, r1[j][1] + (e1_1 - e1_cur));
                r1[j][2] = std::max(0.0, r1[j][2] + (e1_2 - e1_cur));
                for (int a = 0; a < 3; ++a) s1[j][a] += t * p1[j][a];
            }
        }
    }

    DeepPreflopSolution sol;
    for (int i = 0; i < 169; ++i) {
        double t0 = s0[i][0] + s0[i][1];
        sol.open_prob[i] = t0 > 0 ? s0[i][1] / t0 : 0.0;
        double t1 = s1[i][0] + s1[i][1] + s1[i][2];
        sol.vs_open_call[i] = t1 > 0 ? s1[i][1] / t1 : 0.0;
        sol.vs_open_3bet[i] = t1 > 0 ? s1[i][2] / t1 : 0.0;
        double t2 = s2[i][0] + s2[i][1] + s2[i][2];
        sol.vs_3bet_call[i] = t2 > 0 ? s2[i][1] / t2 : 0.0;
        sol.vs_3bet_4bet[i] = t2 > 0 ? s2[i][2] / t2 : 0.0;
    }
    return sol;
}

static double SHOWDOWN_W[7][7];
static double TRANS_FLOP_TURN[7][4][7][4];
static double TRANS_TURN_RIVER[7][4][7];

static std::array<int, 2> classify_post_cpp(int c0, int c1, const int* board, int nb) {
    int r0 = c0 >> 2, r1 = c1 >> 2, s0 = c0 & 3, s1 = c1 & 3;
    int hi_r = std::max(r0, r1), lo_r = std::min(r0, r1);
    int all[7]; all[0] = c0; all[1] = c1;
    for (int i = 0; i < nb; ++i) all[2 + i] = board[i];
    int ev_tot = eval_cards(all, 2 + nb);
    int cat_tot = ev_tot >> 20;

    int bcnt[13] = {0}, spb[4] = {0}, mask_b = 0, max_b = 0;
    for (int i = 0; i < nb; ++i) {
        int br = board[i] >> 2;
        bcnt[br]++;
        spb[board[i] & 3]++;
        mask_b |= (1 << br);
        if (br > max_b) max_b = br;
    }
    int pares_m = 0; bool trio_m = false;
    for (int r = 0; r < 13; ++r) {
        if (bcnt[r] >= 2) pares_m++;
        if (bcnt[r] >= 3) trio_m = true;
    }
    int cat_prop = cat_tot;
    if (cat_tot == 2) cat_prop = (pares_m == 1) ? 1 : (pares_m >= 2 ? 0 : 2);
    else if (cat_tot == 1 && pares_m >= 1) cat_prop = 0;
    else if (cat_tot == 3 && trio_m) cat_prop = 0;

    bool solo_mesa = (nb == 5 && cat_tot >= 4 && eval_cards(board, 5) == ev_tot);
    int made = 0;
    if (cat_prop >= 4 && !solo_mesa) made = 6;
    else if (cat_prop == 3) made = 5;
    else if (cat_prop == 2) made = 4;
    else if (cat_prop == 1) {
        if (r0 == r1 && r0 > max_b) made = 3;
        else if ((r0 == max_b && bcnt[r0] == 1) || (r1 == max_b && bcnt[r1] == 1)) {
            int kick = (r0 == max_b) ? r1 : r0;
            made = (kick >= 9) ? 3 : 2;
        } else if (r0 == r1) {
            int seg = -1;
            for (int r = max_b - 1; r >= 0; --r) if (bcnt[r] > 0) { seg = r; break; }
            made = (r0 > seg) ? 2 : 1;
        } else if (bcnt[r0] == 1 || bcnt[r1] == 1) {
            made = 1;
        }
    }

    int draw = 0;
    if (nb < 5 && made < 5) {
        bool fd = false;
        for (int s : {s0, s1}) {
            if (spb[s] + (s0 == s ? 1 : 0) + (s1 == s ? 1 : 0) == 4) { fd = true; break; }
        }
        int st_outs = 0;
        int mask_all = mask_b | (1 << r0) | (1 << r1);
        if (STRAIGHT[mask_all] < 0) {
            bool has_priv = ((mask_b >> r0) & 1) == 0 || ((mask_b >> r1) & 1) == 0;
            if (has_priv) {
                for (int cand = 0; cand < 13; ++cand) {
                    if ((mask_all >> cand) & 1) continue;
                    int sa = STRAIGHT[mask_all | (1 << cand)];
                    if (sa >= 0 && sa > STRAIGHT[mask_b | (1 << cand)]) st_outs++;
                }
            }
        }
        bool over = (hi_r > max_b && lo_r > max_b && hi_r >= 10);
        if (fd && (st_outs >= 1 || over || made >= 1)) draw = 3;
        else if (fd || st_outs >= 2) draw = 2;
        else if (st_outs == 1 || (over && made == 0)) draw = 1;
    }
    return {made, draw};
}

static void calibrate_postflop_dynamics(uint64_t& rng) {
    double sw_wins[7][7] = {{0}}, sw_cnt[7][7] = {{0}};
    double ft_cnt[7][4][7][4] = {{{{0}}}}, ft_tot[7][4] = {{0}};
    double tr_cnt[7][4][7] = {{{0}}}, tr_tot[7][4] = {{0}};

    int deck[52];
    for (int c = 0; c < 52; ++c) deck[c] = c;

    const int DEALS = 250000;
    for (int d = 0; d < DEALS; ++d) {
        for (int i = 0; i < 9; ++i) {
            int j = i + (int)(splitmix64(rng) % (uint64_t)(52 - i));
            std::swap(deck[i], deck[j]);
        }
        int a0 = deck[0], a1 = deck[1], b0 = deck[2], b1 = deck[3];
        const int* board = &deck[4];
        auto pf = classify_post_cpp(a0, a1, board, 3);
        auto pt = classify_post_cpp(a0, a1, board, 4);
        auto pr = classify_post_cpp(a0, a1, board, 5);
        auto qr = classify_post_cpp(b0, b1, board, 5);

        ft_cnt[pf[0]][pf[1]][pt[0]][pt[1]] += 1.0;
        ft_tot[pf[0]][pf[1]] += 1.0;
        tr_cnt[pt[0]][pt[1]][pr[0]] += 1.0;
        tr_tot[pt[0]][pt[1]] += 1.0;

        int ha[7] = {a0, a1, board[0], board[1], board[2], board[3], board[4]};
        int hb[7] = {b0, b1, board[0], board[1], board[2], board[3], board[4]};
        int va = eval_cards(ha, 7), vb = eval_cards(hb, 7);
        double res = (va > vb) ? 1.0 : (va == vb ? 0.5 : 0.0);
        sw_wins[pr[0]][qr[0]] += res;
        sw_cnt[pr[0]][qr[0]] += 1.0;
        sw_wins[qr[0]][pr[0]] += (1.0 - res);
        sw_cnt[qr[0]][pr[0]] += 1.0;
    }

    for (int i = 0; i < 7; ++i) {
        for (int j = 0; j < 7; ++j) {
            SHOWDOWN_W[i][j] = sw_cnt[i][j] > 0 ? sw_wins[i][j] / sw_cnt[i][j] : (i > j ? 0.9 : (i < j ? 0.1 : 0.5));
        }
    }
    for (int m = 0; m < 7; ++m) {
        for (int dr = 0; dr < 4; ++dr) {
            for (int nm = 0; nm < 7; ++nm) {
                for (int nd = 0; nd < 4; ++nd) {
                    TRANS_FLOP_TURN[m][dr][nm][nd] = ft_tot[m][dr] > 0 ? ft_cnt[m][dr][nm][nd] / ft_tot[m][dr] : (nm == m && nd == dr ? 1.0 : 0.0);
                }
                TRANS_TURN_RIVER[m][dr][nm] = tr_tot[m][dr] > 0 ? tr_cnt[m][dr][nm] / tr_tot[m][dr] : (nm == m ? 1.0 : 0.0);
            }
        }
    }
}

static const int N_POST_INFOS = 3 * 3 * 2 * 2 * 7 * 4 * 5;
static double POST_REG[N_POST_INFOS][3];
static double POST_STRAT[N_POST_INFOS][3];

static inline int info_idx(int st, int spr, int pos, int mw, int made, int draw, int fac) {
    return (((((st * 3 + spr) * 2 + pos) * 2 + mw) * 7 + made) * 4 + draw) * 5 + fac;
}

static void get_strat(int idx, double out[3]) {
    double s = 0.0;
    for (int a = 0; a < 3; ++a) {
        out[a] = POST_REG[idx][a] > 0.0 ? POST_REG[idx][a] : 0.0;
        s += out[a];
    }
    if (s > 1e-12) {
        for (int a = 0; a < 3; ++a) out[a] /= s;
    } else {
        out[0] = 0.34; out[1] = 0.33; out[2] = 0.33;
    }
}

static int sample_discrete(const double* probs, int n, uint64_t& rng) {
    double u = (double)(splitmix64(rng) >> 11) * (1.0 / 9007199254740992.0);
    double acc = 0.0;
    for (int i = 0; i < n; ++i) {
        acc += probs[i];
        if (u <= acc) return i;
    }
    return n - 1;
}

struct PostState {
    int street;
    int spr;
    int mw;
    double pot;
    double eff_stack;
    double invested[2];
    int made[2], draw[2];
    int to_act;
    int raises;
    int checks;
    double pending_bet;
    int last_bet_sz;
};

static double mccfr_traverse(PostState s, int traverser, int iter, uint64_t& rng) {
    int p = s.to_act;
    int fac = 0;
    if (s.pending_bet > 1e-6) {
        if (s.raises >= 2) fac = 4;
        else fac = (s.last_bet_sz == 0) ? 2 : 3;
    } else {
        fac = (s.checks > 0 || p == 1) ? 1 : 0;
    }

    int idx = info_idx(s.street, s.spr, p, s.mw, s.made[p], s.street == 2 ? 0 : s.draw[p], fac);
    double strat[3];
    get_strat(idx, strat);

    bool can_act2 = (s.pending_bet <= 1e-6) ? (s.eff_stack > 0.1 * s.pot) : (s.raises < 2 && s.eff_stack > s.pending_bet + 0.1 * s.pot);
    if (!can_act2 && strat[2] > 0) {
        double rem = strat[0] + strat[1];
        if (rem > 1e-9) { strat[0] /= rem; strat[1] /= rem; strat[2] = 0.0; }
        else { strat[0] = 0.5; strat[1] = 0.5; strat[2] = 0.0; }
    }

    auto step_action = [&](int a) -> double {
        PostState ns = s;
        if (s.pending_bet > 1e-6) {
            if (a == 0) {
                int winner = 1 - p;
                return (winner == traverser) ? (s.pot - s.invested[traverser]) : (-s.invested[traverser]);
            } else if (a == 1) {
                double call_amt = std::min(s.pending_bet, s.eff_stack);
                ns.pot += call_amt;
                ns.invested[p] += call_amt;
                ns.eff_stack -= call_amt;
                ns.pending_bet = 0.0;
                ns.raises = 0; ns.checks = 0;
                if (ns.street == 2 || ns.eff_stack < 0.05 * ns.pot) {
                    int m0 = ns.made[0], d0 = ns.draw[0], m1 = ns.made[1], d1 = ns.draw[1];
                    if (ns.street == 0) {
                        int k0 = sample_discrete(&TRANS_FLOP_TURN[m0][d0][0][0], 28, rng);
                        m0 = k0 / 4; d0 = k0 % 4;
                        int k1 = sample_discrete(&TRANS_FLOP_TURN[m1][d1][0][0], 28, rng);
                        m1 = k1 / 4; d1 = k1 % 4;
                        m0 = sample_discrete(TRANS_TURN_RIVER[m0][d0], 7, rng);
                        m1 = sample_discrete(TRANS_TURN_RIVER[m1][d1], 7, rng);
                    } else if (ns.street == 1) {
                        m0 = sample_discrete(TRANS_TURN_RIVER[m0][d0], 7, rng);
                        m1 = sample_discrete(TRANS_TURN_RIVER[m1][d1], 7, rng);
                    }
                    double w = SHOWDOWN_W[m0][m1];
                    if (traverser == 1) w = 1.0 - w;
                    if (ns.mw == 1) w = std::pow(w, 1.32);
                    return w * ns.pot - ns.invested[traverser];
                }
                if (ns.street == 0) {
                    int k0 = sample_discrete(&TRANS_FLOP_TURN[ns.made[0]][ns.draw[0]][0][0], 28, rng);
                    ns.made[0] = k0 / 4; ns.draw[0] = k0 % 4;
                    int k1 = sample_discrete(&TRANS_FLOP_TURN[ns.made[1]][ns.draw[1]][0][0], 28, rng);
                    ns.made[1] = k1 / 4; ns.draw[1] = k1 % 4;
                } else {
                    ns.made[0] = sample_discrete(TRANS_TURN_RIVER[ns.made[0]][ns.draw[0]], 7, rng);
                    ns.made[1] = sample_discrete(TRANS_TURN_RIVER[ns.made[1]][ns.draw[1]], 7, rng);
                    ns.draw[0] = ns.draw[1] = 0;
                }
                ns.street++;
                ns.to_act = 0;
                return mccfr_traverse(ns, traverser, iter, rng);
            } else {
                double call_amt = std::min(s.pending_bet, s.eff_stack);
                double raise_sz = std::min(s.eff_stack - call_amt, 0.75 * (s.pot + call_amt));
                double put = call_amt + raise_sz;
                ns.pot += put;
                ns.invested[p] += put;
                ns.eff_stack -= put;
                ns.pending_bet = raise_sz;
                ns.raises++;
                ns.last_bet_sz = 1;
                ns.to_act = 1 - p;
                return mccfr_traverse(ns, traverser, iter, rng);
            }
        } else {
            if (a == 0) {
                ns.checks++;
                if (ns.checks >= 2) {
                    ns.checks = 0; ns.raises = 0; ns.pending_bet = 0.0;
                    if (ns.street == 2) {
                        double w = SHOWDOWN_W[ns.made[0]][ns.made[1]];
                        if (traverser == 1) w = 1.0 - w;
                        if (ns.mw == 1) w = std::pow(w, 1.32);
                        return w * ns.pot - ns.invested[traverser];
                    }
                    if (ns.street == 0) {
                        int k0 = sample_discrete(&TRANS_FLOP_TURN[ns.made[0]][ns.draw[0]][0][0], 28, rng);
                        ns.made[0] = k0 / 4; ns.draw[0] = k0 % 4;
                        int k1 = sample_discrete(&TRANS_FLOP_TURN[ns.made[1]][ns.draw[1]][0][0], 28, rng);
                        ns.made[1] = k1 / 4; ns.draw[1] = k1 % 4;
                    } else {
                        ns.made[0] = sample_discrete(TRANS_TURN_RIVER[ns.made[0]][ns.draw[0]], 7, rng);
                        ns.made[1] = sample_discrete(TRANS_TURN_RIVER[ns.made[1]][ns.draw[1]], 7, rng);
                        ns.draw[0] = ns.draw[1] = 0;
                    }
                    ns.street++;
                    ns.to_act = 0;
                    return mccfr_traverse(ns, traverser, iter, rng);
                }
                ns.to_act = 1 - p;
                return mccfr_traverse(ns, traverser, iter, rng);
            } else {
                double frac = (a == 1) ? 0.45 : 0.85;
                double bet = std::min(s.eff_stack, frac * s.pot);
                ns.pot += bet;
                ns.invested[p] += bet;
                ns.eff_stack -= bet;
                ns.pending_bet = bet;
                ns.raises = 1;
                ns.last_bet_sz = (a == 1) ? 0 : 1;
                ns.to_act = 1 - p;
                return mccfr_traverse(ns, traverser, iter, rng);
            }
        }
    };

    int max_a = can_act2 ? 3 : 2;
    if (p == traverser) {
        double util[3] = {0.0, 0.0, 0.0};
        double node_u = 0.0;
        for (int a = 0; a < max_a; ++a) {
            if (iter > 150000 && POST_REG[idx][a] < -250.0 && (splitmix64(rng) % 20) != 0) {
                util[a] = 0.0;
                continue;
            }
            util[a] = step_action(a);
            node_u += strat[a] * util[a];
        }
        for (int a = 0; a < max_a; ++a) {
            POST_REG[idx][a] = std::max(-1e6, POST_REG[idx][a] + (util[a] - node_u));
        }
        return node_u;
    } else {
        int a = sample_discrete(strat, max_a, rng);
        for (int k = 0; k < max_a; ++k) {
            POST_STRAT[idx][k] += (double)iter * strat[k];
        }
        return step_action(a);
    }
}

int main() {
    init_eval();
    uint64_t rng = 0x20261120CAFEBABEULL;

    std::cout << "1/4 Building 169 hand classes & 169x169 equity matrix...\n";
    build_classes(rng);
    compute_eq169(rng);

    std::cout << "2/4 Solving Push/Fold & Deep Preflop blueprints via CFR+...\n";
    std::array<double, 4> pf_stacks = {5.0, 8.0, 11.0, 14.0};
    std::vector<PushFoldSolution> pf_hu, pf_mw;
    for (double sb : pf_stacks) {
        pf_hu.push_back(solve_push_fold_cfrp(sb, 1, 3000));
        pf_mw.push_back(solve_push_fold_cfrp(sb, 3, 3000));
    }
    auto dp_ep = solve_deep_preflop_cfrp(4, 50.0, 2500);
    auto dp_co = solve_deep_preflop_cfrp(2, 50.0, 2500);
    auto dp_btn = solve_deep_preflop_cfrp(1, 50.0, 2500);

    std::cout << "3/4 Calibrating real Hold'em flop->turn->river transitions...\n";
    calibrate_postflop_dynamics(rng);

    std::cout << "4/4 Running External-Sampling MCCFR postflop (3,000,000 iterations)...\n";
    const int ITERS = 3000000;
    for (int it = 1; it <= ITERS; ++it) {
        int spr = (int)(splitmix64(rng) % 3);
        int mw = (int)(splitmix64(rng) % 2);
        double spr_val = (spr == 0) ? 1.8 : (spr == 1 ? 4.2 : 9.0);
        auto sample_flop_hand = [&](int& m, int& dr) {
            int u = (int)(splitmix64(rng) % 100);
            if (u < 34) m = 0;
            else if (u < 54) m = 1;
            else if (u < 70) m = 2;
            else if (u < 84) m = 3;
            else if (u < 92) m = 4;
            else if (u < 96) m = 5;
            else m = 6;
            int d = (int)(splitmix64(rng) % 100);
            if (m >= 5) dr = 0;
            else if (d < 62) dr = 0;
            else if (d < 82) dr = 1;
            else if (d < 95) dr = 2;
            else dr = 3;
        };
        PostState s{};
        s.street = (int)(splitmix64(rng) % 3); // sample starting street too so turn/river infosets get full coverage
        s.spr = spr; s.mw = mw;
        s.pot = 1.0; s.eff_stack = spr_val;
        s.invested[0] = s.invested[1] = 0.5;
        sample_flop_hand(s.made[0], s.draw[0]);
        sample_flop_hand(s.made[1], s.draw[1]);
        if (s.street == 2) s.draw[0] = s.draw[1] = 0;
        s.to_act = 0;
        mccfr_traverse(s, it & 1, it, rng);
    }

    std::ofstream out("blueprint_data.py");
    out << "\"\"\"Blueprint precomputado offline con CFR+ y External-Sampling MCCFR.\n\n";
    out << "GENERADO por tools/trainer_cfr.cpp (no editar a mano).\n";
    out << "Python puro sin dependencias externas.\n\"\"\"\n\n";

    // Export exact HAND_ORDER matching C++ index 0..168
    out << "HAND_ORDER = (\n    ";
    for (int i = 0; i < 169; ++i) {
        if (i > 0 && i % 6 == 0) out << "\n    ";
        out << "(" << CLASSES[i].hi << ", " << CLASSES[i].lo << ", " << (CLASSES[i].suited ? "True" : "False") << ")" << (i < 168 ? ", " : "");
    }
    out << "\n)\n\n";

    out << "PUSH_FOLD_STACKS = (5, 8, 11, 14)\n";
    out << "PUSH_FOLD = {\n";
    for (int mw = 0; mw <= 1; ++mw) {
        const auto& vec = mw ? pf_mw : pf_hu;
        for (size_t k = 0; k < pf_stacks.size(); ++k) {
            out << "    (" << mw << ", " << (int)pf_stacks[k] << "): (\n        (";
            for (int i = 0; i < 169; ++i) {
                if (i > 0 && i % 13 == 0) out << "\n         ";
                char buf[16]; std::snprintf(buf, sizeof(buf), "%.3f", vec[k].push_prob[i]);
                out << buf << (i < 168 ? ", " : "");
            }
            out << "),\n        (";
            for (int i = 0; i < 169; ++i) {
                if (i > 0 && i % 13 == 0) out << "\n         ";
                char buf[16]; std::snprintf(buf, sizeof(buf), "%.3f", vec[k].call_prob[i]);
                out << buf << (i < 168 ? ", " : "");
            }
            out << "),\n    ),\n";
        }
    }
    out << "}\n\n";

    auto write_deep = [&](const char* name, const DeepPreflopSolution& d) {
        out << "    \"" << name << "\": (\n";
        const std::array<double, 169>* arrs[5] = {&d.open_prob, &d.vs_open_call, &d.vs_open_3bet, &d.vs_3bet_call, &d.vs_3bet_4bet};
        for (int m = 0; m < 5; ++m) {
            out << "        (";
            for (int i = 0; i < 169; ++i) {
                if (i > 0 && i % 13 == 0) out << "\n         ";
                char buf[16]; std::snprintf(buf, sizeof(buf), "%.3f", (*arrs[m])[i]);
                out << buf << (i < 168 ? ", " : "");
            }
            out << "),\n";
        }
        out << "    ),\n";
    };
    out << "PREFLOP_DEEP = {\n";
    write_deep("EP", dp_ep);
    write_deep("CO", dp_co);
    write_deep("BTN", dp_btn);
    out << "}\n\n";

    out << "POSTFLOP_BP = {\n";
    for (int st = 0; st < 3; ++st) {
        for (int spr = 0; spr < 3; ++spr) {
            for (int pos = 0; pos < 2; ++pos) {
                for (int mw = 0; mw < 2; ++mw) {
                    for (int made = 0; made < 7; ++made) {
                        int max_dr = (st == 2) ? 1 : 4;
                        for (int dr = 0; dr < max_dr; ++dr) {
                            for (int fac = 0; fac < 5; ++fac) {
                                int idx = info_idx(st, spr, pos, mw, made, dr, fac);
                                double s = POST_STRAT[idx][0] + POST_STRAT[idx][1] + POST_STRAT[idx][2];
                                double p0 = 0.5, p1 = 0.5, p2 = 0.0;
                                if (s > 1e-9) {
                                    p0 = POST_STRAT[idx][0] / s;
                                    p1 = POST_STRAT[idx][1] / s;
                                    p2 = POST_STRAT[idx][2] / s;
                                } else {
                                    double tmp[3]; get_strat(idx, tmp);
                                    p0 = tmp[0]; p1 = tmp[1]; p2 = tmp[2];
                                }
                                char buf[96];
                                std::snprintf(buf, sizeof(buf),
                                    "    (%d, %d, %d, %d, %d, %d, %d): (%.3f, %.3f, %.3f),\n",
                                    st, spr, pos, mw, made, dr, fac, p0, p1, p2);
                                out << buf;
                            }
                        }
                    }
                }
            }
        }
    }
    out << "}\n";
    out.close();
    std::cout << "Done! Wrote blueprint_data.py\n";
    return 0;
}
