#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <ctype.h>

static int is_hex_secret(const unsigned char *buf, size_t n, size_t pos, size_t *end) {
    const char *p = "secret{";
    if (pos + 8 >= n) return 0;
    if (memcmp(buf + pos, p, 7) != 0) return 0;
    size_t i = pos + 7;
    int digits = 0;
    while (i < n && isxdigit(buf[i])) {
        i++;
        digits++;
    }
    if (digits > 0 && i < n && buf[i] == '}') {
        *end = i + 1;
        return 1;
    }
    return 0;
}

static void scan_bytes(const char *label, const unsigned char *buf, size_t n) {
    for (size_t i = 0; i < n; i++) {
        size_t end = 0;
        if (is_hex_secret(buf, n, i, &end)) {
            printf("FOUND %s offset %zu: ", label, i);
            fwrite(buf + i, 1, end - i, stdout);
            putchar('\n');
        }
    }
}

static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int32_t sle32(const unsigned char *p) {
    return (int32_t)le32(p);
}

static uint16_t le16(const unsigned char *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void pack_and_scan(const char *name, const unsigned char *pix, int w, int h, int nchan,
                          const int *order, int olen, int plane, int msb) {
    size_t max_bits = (size_t)w * (size_t)h * (size_t)olen;
    size_t outn = max_bits / 8;
    unsigned char *out = calloc(outn ? outn : 1, 1);
    if (!out) return;
    size_t oi = 0;
    int bitn = 0;
    unsigned char b = 0;
    for (int y = 0; y < h; y++) {
        const unsigned char *row = pix + (size_t)y * (size_t)w * (size_t)nchan;
        for (int x = 0; x < w; x++) {
            const unsigned char *px = row + (size_t)x * (size_t)nchan;
            for (int j = 0; j < olen; j++) {
                int bit = (px[order[j]] >> plane) & 1;
                if (msb) b = (unsigned char)((b << 1) | bit);
                else b = (unsigned char)(b | (bit << bitn));
                bitn++;
                if (bitn == 8) {
                    out[oi++] = b;
                    b = 0;
                    bitn = 0;
                }
            }
        }
    }
    char label[256];
    snprintf(label, sizeof(label), "%s p%d o", name, plane);
    size_t len = strlen(label);
    for (int j = 0; j < olen && len + 4 < sizeof(label); j++) {
        len += snprintf(label + len, sizeof(label) - len, "%d", order[j]);
    }
    snprintf(label + strlen(label), sizeof(label) - strlen(label), " %s", msb ? "msb" : "lsb");
    scan_bytes(label, out, oi);
    free(out);
}

int main(int argc, char **argv) {
    for (int ai = 1; ai < argc; ai++) {
        const char *path = argv[ai];
        FILE *f = fopen(path, "rb");
        if (!f) { perror(path); continue; }
        fseek(f, 0, SEEK_END);
        long flen = ftell(f);
        fseek(f, 0, SEEK_SET);
        unsigned char *d = malloc((size_t)flen);
        if (!d || fread(d, 1, (size_t)flen, f) != (size_t)flen) { fclose(f); free(d); continue; }
        fclose(f);
        scan_bytes(path, d, (size_t)flen);
        if (flen < 54 || d[0] != 'B' || d[1] != 'M') { free(d); continue; }
        uint32_t off = le32(d + 10);
        int32_t w = sle32(d + 18);
        int32_t sh = sle32(d + 22);
        int h = sh < 0 ? -sh : sh;
        int topdown = sh < 0;
        int bpp = le16(d + 28);
        int nchan = bpp / 8;
        if (w <= 0 || h <= 0 || (nchan != 3 && nchan != 4)) { free(d); continue; }
        size_t stride = (((size_t)w * (size_t)bpp + 31) / 32) * 4;
        unsigned char *pix = malloc((size_t)w * (size_t)h * (size_t)nchan);
        if (!pix) { free(d); continue; }
        for (int y = 0; y < h; y++) {
            int sy = topdown ? y : h - 1 - y;
            memcpy(pix + (size_t)y * (size_t)w * (size_t)nchan, d + off + (size_t)sy * stride, (size_t)w * (size_t)nchan);
        }
        printf("SCAN %s %dx%d %dch\n", path, w, h, nchan);
        int orders[9][4] = {
            {0,-1,-1,-1}, {1,-1,-1,-1}, {2,-1,-1,-1}, {3,-1,-1,-1},
            {0,1,2,-1}, {2,1,0,-1}, {0,1,2,3}, {2,1,0,3}, {3,2,1,0}
        };
        int lens[9] = {1,1,1,1,3,3,4,4,4};
        int count = nchan == 3 ? 6 : 9;
        for (int plane = 0; plane < 8; plane++) {
            for (int oi = 0; oi < count; oi++) {
                if (orders[oi][0] >= nchan) continue;
                pack_and_scan(path, pix, w, h, nchan, orders[oi], lens[oi], plane, 1);
                pack_and_scan(path, pix, w, h, nchan, orders[oi], lens[oi], plane, 0);
            }
        }
        free(pix);
        free(d);
    }
    return 0;
}
