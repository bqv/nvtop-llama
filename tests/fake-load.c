/* Reads a GGUF the way a cold llama.cpp load does, so the meter's LOAD bar can
 * be tested deterministically and without touching a real model server.
 *
 * O_DIRECT matters: read_bytes in /proc/<pid>/io counts bytes fetched from the
 * block layer, so a page-cached read would leave it at zero -- exactly the trap
 * that makes mmap loading useless as a progress signal.
 *
 * argv[0] must be named llama-server, and the router passes --model.
 *   llama-server --model ./X.gguf [other args ignored]
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv) {
  const char *path = NULL;
  /* Paced reads: a real cold load of a 16 GiB GGUF also takes tens of seconds,
   * and hammering the disk at full speed starves the very UI being tested. */
  size_t blk = 16u << 20;
  struct timespec pace = {0, 50 * 1000 * 1000}; /* 50 ms between chunks */
  void *buf;
  int fd;
  ssize_t n;
  unsigned long long total = 0;

  /* Also usable as a fake sd-server, which names its weights the same way. */
  static const char *flags[] = {"--model", "--diffusion-model", "--vae", "--llm"};
  const char *paths[4];
  int npaths = 0;
  for (int i = 1; i + 1 < argc && npaths < 4; i++)
    for (unsigned f = 0; f < sizeof(flags) / sizeof(flags[0]); f++)
      if (strcmp(argv[i], flags[f]) == 0)
        paths[npaths++] = argv[i + 1];
  path = npaths ? paths[0] : NULL;
  if (!path) {
    fprintf(stderr, "fake-load: no --model\n");
    return 2;
  }
  fd = open(path, O_RDONLY | O_DIRECT);
  if (fd < 0) {
    perror("fake-load: open");
    return 1;
  }
  if (posix_memalign(&buf, 4096, blk) != 0)
    return 1;
  {
    /* Sync point for the test: stdout is a pipe, so the reader learns that a
     * known amount has been read before it starts the UI. Without this the
     * first captured frame can show 0% and the test races. No polling, no
     * sleeping process -- the reader blocks on the pipe. */
    const char *want = getenv("FAKELOAD_READY_BYTES");
    unsigned long long ready_at = want ? strtoull(want, NULL, 10) : (512ull << 20);
    int said = 0;
    for (int k = 0; k < npaths; k++) {
      if (k > 0) {
        fd = open(paths[k], O_RDONLY | O_DIRECT);
        if (fd < 0)
          continue;
      }
      while ((n = read(fd, buf, blk)) > 0) {
        total += (unsigned long long)n;
        if (!said && total >= ready_at) {
          printf("ready\n");
          fflush(stdout);
          said = 1;
        }
        nanosleep(&pace, NULL);
      }
      if (k < npaths - 1)
        close(fd);
    }
    if (!said) {
      printf("ready\n");
      fflush(stdout);
    }
  }
  fprintf(stderr, "fake-load: read %llu bytes\n", total);
  free(buf);
  close(fd);
  /* FAKELOAD_PAUSE keeps the child alive with a static read_bytes, which is what
   * mmap loading looks like from /proc -- the meter must call that stalled
   * rather than draw a frozen bar. pause() blocks on a signal, not on a clock. */
  if (getenv("FAKELOAD_PAUSE"))
    pause();
  return 0;
}
