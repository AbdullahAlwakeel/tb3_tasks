#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jpeglib.h>

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s input.jpg outdir\n", argv[0]);
        return 1;
    }
    FILE *in = fopen(argv[1], "rb");
    if (!in) { perror(argv[1]); return 1; }
    struct jpeg_decompress_struct cinfo;
    struct jpeg_error_mgr jerr;
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_decompress(&cinfo);
    jpeg_stdio_src(&cinfo, in);
    jpeg_read_header(&cinfo, TRUE);
    jvirt_barray_ptr *coef_arrays = jpeg_read_coefficients(&cinfo);
    for (int ci = 0; ci < cinfo.num_components; ci++) {
        jpeg_component_info *comp = cinfo.comp_info + ci;
        for (int k = 0; k < DCTSIZE2; k++) {
            char path[512];
            snprintf(path, sizeof(path), "%s/c%d_k%02d.pgm", argv[2], ci, k);
            FILE *out = fopen(path, "wb");
            if (!out) { perror(path); continue; }
            fprintf(out, "P5\n%u %u\n255\n", comp->width_in_blocks, comp->height_in_blocks);
            for (JDIMENSION by = 0; by < comp->height_in_blocks; by++) {
                JBLOCKARRAY row = (*cinfo.mem->access_virt_barray)
                    ((j_common_ptr)&cinfo, coef_arrays[ci], by, 1, FALSE);
                for (JDIMENSION bx = 0; bx < comp->width_in_blocks; bx++) {
                    int v = row[0][bx][k];
                    unsigned char px = (abs(v) & 1) ? 255 : 0;
                    fwrite(&px, 1, 1, out);
                }
            }
            fclose(out);
            snprintf(path, sizeof(path), "%s/c%d_k%02d_sign.pgm", argv[2], ci, k);
            out = fopen(path, "wb");
            if (!out) { perror(path); continue; }
            fprintf(out, "P5\n%u %u\n255\n", comp->width_in_blocks, comp->height_in_blocks);
            for (JDIMENSION by = 0; by < comp->height_in_blocks; by++) {
                JBLOCKARRAY row = (*cinfo.mem->access_virt_barray)
                    ((j_common_ptr)&cinfo, coef_arrays[ci], by, 1, FALSE);
                for (JDIMENSION bx = 0; bx < comp->width_in_blocks; bx++) {
                    int v = row[0][bx][k];
                    unsigned char px = v < 0 ? 255 : 0;
                    fwrite(&px, 1, 1, out);
                }
            }
            fclose(out);
            snprintf(path, sizeof(path), "%s/c%d_k%02d_nz.pgm", argv[2], ci, k);
            out = fopen(path, "wb");
            if (!out) { perror(path); continue; }
            fprintf(out, "P5\n%u %u\n255\n", comp->width_in_blocks, comp->height_in_blocks);
            for (JDIMENSION by = 0; by < comp->height_in_blocks; by++) {
                JBLOCKARRAY row = (*cinfo.mem->access_virt_barray)
                    ((j_common_ptr)&cinfo, coef_arrays[ci], by, 1, FALSE);
                for (JDIMENSION bx = 0; bx < comp->width_in_blocks; bx++) {
                    int v = row[0][bx][k];
                    unsigned char px = v != 0 ? 255 : 0;
                    fwrite(&px, 1, 1, out);
                }
            }
            fclose(out);
        }
    }
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);
    fclose(in);
    return 0;
}
