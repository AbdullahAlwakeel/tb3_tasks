#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <jpeglib.h>

static int match_secret(const unsigned char *buf, size_t n, size_t pos, size_t *end) {
    if (pos + 8 >= n || memcmp(buf + pos, "secret{", 7) != 0) return 0;
    size_t i = pos + 7;
    int digits = 0;
    while (i < n && isxdigit(buf[i])) { i++; digits++; }
    if (digits && i < n && buf[i] == '}') { *end = i + 1; return 1; }
    return 0;
}

static void scan(const char *label, const unsigned char *buf, size_t n) {
    for (size_t i = 0; i < n; i++) {
        size_t end = 0;
        if (match_secret(buf, n, i, &end)) {
            printf("FOUND %s offset %zu: ", label, i);
            fwrite(buf + i, 1, end - i, stdout);
            putchar('\n');
        }
    }
}

static void append_bit(unsigned char **out, size_t *n, size_t *cap, int *bitn, unsigned char *cur, int bit, int msb) {
    if (*n >= *cap) {
        *cap = *cap ? *cap * 2 : 4096;
        *out = realloc(*out, *cap);
        if (!*out) exit(2);
    }
    if (msb) *cur = (unsigned char)((*cur << 1) | (bit & 1));
    else *cur = (unsigned char)(*cur | ((bit & 1) << *bitn));
    (*bitn)++;
    if (*bitn == 8) {
        (*out)[(*n)++] = *cur;
        *cur = 0;
        *bitn = 0;
    }
}

static void build_stream(struct jpeg_decompress_struct *cinfo, jvirt_barray_ptr *coef_arrays,
                         int mode, int msb, unsigned char **out, size_t *outn) {
    size_t cap = 0, n = 0;
    unsigned char *buf = NULL, cur = 0;
    int bitn = 0;
    for (int ci = 0; ci < cinfo->num_components; ci++) {
        jpeg_component_info *compptr = cinfo->comp_info + ci;
        for (JDIMENSION by = 0; by < compptr->height_in_blocks; by++) {
            JBLOCKARRAY row = (*cinfo->mem->access_virt_barray)
                ((j_common_ptr)cinfo, coef_arrays[ci], by, 1, FALSE);
            for (JDIMENSION bx = 0; bx < compptr->width_in_blocks; bx++) {
                JCOEFPTR block = row[0][bx];
                int start = (mode == 2) ? 0 : 1;
                for (int k = start; k < DCTSIZE2; k++) {
                    int v = block[k];
                    if (mode == 0 && (v == 0 || v == 1 || v == -1)) continue; /* jsteg-like */
                    if (mode == 1 && v == 0) continue;
                    int bit = abs(v) & 1;
                    append_bit(&buf, &n, &cap, &bitn, &cur, bit, msb);
                }
            }
        }
    }
    *out = buf;
    *outn = n;
}

int main(int argc, char **argv) {
    for (int ai = 1; ai < argc; ai++) {
        FILE *in = fopen(argv[ai], "rb");
        if (!in) { perror(argv[ai]); continue; }
        struct jpeg_decompress_struct cinfo;
        struct jpeg_error_mgr jerr;
        cinfo.err = jpeg_std_error(&jerr);
        jpeg_create_decompress(&cinfo);
        jpeg_stdio_src(&cinfo, in);
        jpeg_read_header(&cinfo, TRUE);
        jvirt_barray_ptr *coef_arrays = jpeg_read_coefficients(&cinfo);
        printf("SCAN %s components=%d\n", argv[ai], cinfo.num_components);
        for (int mode = 0; mode < 3; mode++) {
            for (int msb = 0; msb < 2; msb++) {
                unsigned char *buf = NULL;
                size_t n = 0;
                build_stream(&cinfo, coef_arrays, mode, msb, &buf, &n);
                char label[128];
                snprintf(label, sizeof(label), "%s mode%d %s", argv[ai], mode, msb ? "msb" : "lsb");
                scan(label, buf, n);
                free(buf);
            }
        }
        jpeg_finish_decompress(&cinfo);
        jpeg_destroy_decompress(&cinfo);
        fclose(in);
    }
    return 0;
}
