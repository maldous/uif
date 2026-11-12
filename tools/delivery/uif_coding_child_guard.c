/* UIF Linux x86_64 coding-child guard.
 * Adapted from maldous/offmenu 5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e
 * tools/delivery/coding_child_guard.c (SHA-256
 * 0226287dcc45e205101353e149e15d632a070eeee514e941f272665eb88d4e4e).
 * Build only with a reviewed, fixed binding header. This binary is never setuid.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/netlink.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <limits.h>


#if !defined(__linux__) || !defined(__x86_64__) || defined(__ILP32__)
#error "UIF guard requires the Linux x86_64 ABI"
#endif
#if !defined(UIF_UID) || !defined(UIF_GID) || !defined(UIF_PRINCIPAL) || \
    !defined(UIF_HOME) || !defined(UIF_RUNTIME_ROOT) || !defined(UIF_WORK_ROOT) || \
    !defined(UIF_REVIEW_ROOT) || !defined(UIF_OUTPUT_ROOT) || !defined(UIF_AUTH_REFERENCE) || \
    !defined(UIF_NATIVE) || !defined(UIF_NATIVE_SHA256) || !defined(UIF_DESCRIPTOR_SHA256) || !defined(UIF_ROLE_MODE)
#error "UIF guard requires an explicitly qualified binding header"
#endif
_Static_assert(UIF_UID > 0 && UIF_UID < (uid_t)-1, "non-root UIF UID required");
_Static_assert(UIF_GID > 0 && UIF_GID < (gid_t)-1, "non-root UIF GID required");

#define DESCRIPTOR_SHA256 "e7774528834d5232ece821e6f2d23d91b5cd6b9ec6a8d094f4e0f88b6d933069"

static _Noreturn void refuse(void)
{
    fputs("UIF coding confinement refused\n", stderr);
    exit(126);
}

/* SHA256 for a regular root-controlled executable; no subprocess or package. */
static uint32_t rr(uint32_t x, unsigned n) { return (x>>n)|(x<<(32-n)); }
static void block(uint32_t h[8], const unsigned char *p) {
    static const uint32_t k[64]={
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t w[64],a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
    for(unsigned i=0;i<16;i++) w[i]=(uint32_t)p[4*i]<<24|(uint32_t)p[4*i+1]<<16|(uint32_t)p[4*i+2]<<8|p[4*i+3];
    for(unsigned i=16;i<64;i++) w[i]=w[i-16]+(rr(w[i-15],7)^rr(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(rr(w[i-2],17)^rr(w[i-2],19)^(w[i-2]>>10));
    for(unsigned i=0;i<64;i++) {
        uint32_t t=z+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^(~e&g))+k[i]+w[i];
        uint32_t u=(rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&c)^(b&c));
        z=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;
    }
    h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;
}
static void hash_fd(int fd, char hex[65]) {
    uint32_t h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    unsigned char buf[64]; size_t used=0; uint64_t total=0; ssize_t n;
    while((n=read(fd,buf+used,64-used))>0) {
        used+=(size_t)n; total+=(uint64_t)n;
        if(used==64) { block(h,buf);used=0; }
    }
    if(n<0) refuse();
    buf[used++]=0x80;
    if(used>56) { memset(buf+used,0,64-used);block(h,buf);used=0; }
    memset(buf+used,0,56-used);
    for(unsigned i=0;i<8;i++) buf[63-i]=(unsigned char)((total*8)>>(8*i));
    block(h,buf);
    for(unsigned i=0;i<8;i++) snprintf(hex+8*i,9,"%08x",h[i]);
}

/* Read-only context verification: no setuid, setgroups or security mutation. */
static void context(void) {
    uid_t r,e,s; gid_t gr,ge,gs;
    struct __user_cap_header_struct hdr={_LINUX_CAPABILITY_VERSION_3,0};
    struct __user_cap_data_struct caps[2]={{0}};
    if(getresuid(&r,&e,&s)||getresgid(&gr,&ge,&gs)||r!=UIF_UID||e!=UIF_UID||s!=UIF_UID||
       gr!=UIF_GID||ge!=UIF_GID||gs!=UIF_GID||getgroups(0,NULL)!=0||
       prctl(PR_GET_NO_NEW_PRIVS,0,0,0,0)!=1||syscall(SYS_capget,&hdr,caps)) refuse();
    for(unsigned i=0;i<2;i++) if(caps[i].effective||caps[i].permitted||caps[i].inheritable) refuse();
    for(int i=0;i<=CAP_LAST_CAP;i++)
        if(prctl(PR_CAPBSET_READ,i,0,0,0)!=0||prctl(PR_CAP_AMBIENT,PR_CAP_AMBIENT_IS_SET,i,0,0)!=0) refuse();
    /* Refuse kernels exposing capabilities unknown to these build headers. */
    errno=0;
    if(prctl(PR_CAPBSET_READ,CAP_LAST_CAP+1,0,0,0)!=-1||errno!=EINVAL) refuse();
}
#define DENY(n) BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,(n),0,1), BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM)
/* No pointer inspection. External UNIX sockets cannot be created or imported.
 * Anonymous connected stream pairs support the native runtime's signal pipe.
 * TCP connect stays available; actual provider/DNS compatibility is unobserved.
 */
static struct sock_filter filter[]={
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,arch)),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AUDIT_ARCH_X86_64,1,0),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr)),
    BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,0x40000000,0,1),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
    /* Reject obsolete untagged x32 syscall numbers and unreviewed ABI growth. */
    BPF_JUMP(BPF_JMP|BPF_JGE|BPF_K,512,0,1),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
    DENY(SYS_io_uring_setup), DENY(SYS_io_uring_enter), DENY(SYS_io_uring_register),
    DENY(SYS_ptrace), DENY(SYS_process_vm_readv), DENY(SYS_process_vm_writev), DENY(SYS_pidfd_getfd),
    /* These syscall arguments are C ints: inspect their low 32 bits. */
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_socketpair,0,9),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[0])),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_UNIX,0,6),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[1])),
    BPF_STMT(BPF_ALU|BPF_AND|BPF_K,(uint32_t)~(SOCK_CLOEXEC|SOCK_NONBLOCK)),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SOCK_STREAM,0,3),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[2])),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,0,0,1),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM),
    /* /proc/PID/fd open cannot duplicate sockets; pidfd_getfd is denied above. */
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_socket,0,9),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[0])),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_INET,7,0),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_INET6,6,0),
    /* Bubblewrap's isolated-netns loopback setup needs this exact route
     * socket. Host mutations still require CAP_NET_ADMIN, dropped above. */
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_NETLINK,0,4),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[1])),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SOCK_RAW|SOCK_CLOEXEC,0,2),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[2])),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,NETLINK_ROUTE,1,0),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
};

static int token(const char *value)
{
    size_t size = strlen(value);
    return size && strspn(value,
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") == size;
}

static int absolute_path(const char *path)
{
    size_t size = strlen(path);
    if (size < 2 || size >= PATH_MAX || path[0] != '/' || path[size - 1] == '/')
        return 0;
    for (const char *part = path + 1; *part;) {
        size_t length = strcspn(part, "/");
        if (!length || (length == 1 && part[0] == '.') ||
            (length == 2 && part[0] == '.' && part[1] == '.'))
            return 0;
        for (size_t i = 0; i < length; i++)
            if ((unsigned char)part[i] < 32 || part[i] == 127)
                return 0;
        part += length;
        if (*part)
            part++;
    }
    return 1;
}

static int under(const char *path, const char *root)
{
    size_t n = strlen(root);
    return !strncmp(path, root, n) && (path[n] == '/' || path[n] == '\0');
}

static void check_pins(void)
{
    const char *paths[] = {UIF_HOME, UIF_RUNTIME_ROOT, UIF_WORK_ROOT, UIF_REVIEW_ROOT,
                           UIF_OUTPUT_ROOT, UIF_AUTH_REFERENCE, UIF_NATIVE};
    for (size_t i = 0; i < sizeof(paths) / sizeof(*paths); i++)
        if (!absolute_path(paths[i]))
            refuse();
    if ((strcmp(UIF_ROLE_MODE, "workspace-write") && strcmp(UIF_ROLE_MODE, "read-only"))
        || !token(UIF_PRINCIPAL) || !under(UIF_WORK_ROOT, UIF_RUNTIME_ROOT) ||
        !under(UIF_REVIEW_ROOT, UIF_RUNTIME_ROOT) || !under(UIF_OUTPUT_ROOT, UIF_RUNTIME_ROOT) ||
        under(UIF_AUTH_REFERENCE, UIF_RUNTIME_ROOT) || under(UIF_NATIVE, UIF_RUNTIME_ROOT) ||
        strlen(UIF_NATIVE_SHA256) != 64 ||
        strspn(UIF_NATIVE_SHA256, "0123456789abcdef") != 64 ||
        strcmp(UIF_DESCRIPTOR_SHA256, DESCRIPTOR_SHA256))
        refuse();
}

static void descriptors(void)
{
    for (int fd = 0; fd < 3; fd++) {
        struct stat info;
        int flags = fcntl(fd, F_GETFL);
        if (flags < 0 || fstat(fd, &info))
            refuse();
        if (fd == 0) {
            if (!S_ISFIFO(info.st_mode) || (flags & O_ACCMODE) != O_RDONLY)
                refuse();
        } else if (!S_ISREG(info.st_mode) || (flags & O_ACCMODE) != O_WRONLY ||
                   info.st_uid != UIF_UID || info.st_nlink != 1 || (info.st_mode & 0077)) {
            refuse();
        }
    }
    /* Fail closed on old kernels and retain no ambient descriptors. */
    if (syscall(SYS_close_range, 3U, ~0U, 0))
        refuse();
}

static void validate_dir(int fd, uid_t owner)
{
    struct stat info;
    if (fd < 0 || fstat(fd, &info) || !S_ISDIR(info.st_mode) ||
        info.st_uid != owner || (info.st_mode & 0022))
        refuse();
}

static int next_dir(int parent, const char *name, uid_t owner)
{
    int fd = openat(parent, name, O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    validate_dir(fd, owner);
    close(parent);
    return fd;
}

static int descend_role_slot(int fd, const char *rest, uid_t slot_owner)
{
    char copy[PATH_MAX], *save = NULL;
    strcpy(copy, rest);
    char *slot = strtok_r(copy, "/", &save);
    if (!slot)
        refuse();
    fd = next_dir(fd, slot, slot_owner);
    char *container = strtok_r(NULL, "/", &save);
    if (!container)
        refuse();
    fd = next_dir(fd, container, slot_owner);
    for (char *part = strtok_r(NULL, "/", &save); part; part = strtok_r(NULL, "/", &save))
        fd = next_dir(fd, part, UIF_UID);
    return fd;
}

static int owned_dir(const char *path)
{
    char resolved[PATH_MAX];
    if (!absolute_path(path) || !under(path, UIF_RUNTIME_ROOT) ||
        !realpath(UIF_RUNTIME_ROOT, resolved) || strcmp(resolved, UIF_RUNTIME_ROOT) ||
        !strcmp(path, UIF_RUNTIME_ROOT))
        refuse();
    int fd = open(UIF_RUNTIME_ROOT, O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    validate_dir(fd, 0);
    return descend_role_slot(fd, path + strlen(UIF_RUNTIME_ROOT) + 1, 0);
}

static void verify_native_bytes(int fd)
{
    char digest[65];
    hash_fd(fd, digest);
    if (strcmp(digest, UIF_NATIVE_SHA256))
        refuse();
}

static int native_fd(void)
{
    char copy[PATH_MAX], *save = NULL;
    strcpy(copy, UIF_NATIVE + 1);
    int dir = open("/", O_PATH | O_DIRECTORY | O_CLOEXEC);
    validate_dir(dir, 0);
    char *part = strtok_r(copy, "/", &save);
    char *next;
    while ((next = strtok_r(NULL, "/", &save))) {
        dir = next_dir(dir, part, 0);
        part = next;
    }
    int fd = openat(dir, part, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    close(dir);
    struct stat info;
    if (fd < 0 || fstat(fd, &info) || info.st_uid != 0 || info.st_nlink != 1 ||
        !S_ISREG(info.st_mode) || !(info.st_mode & S_IXUSR) || (info.st_mode & 06022))
        refuse();
    verify_native_bytes(fd);
    return fd;
}

static const char *leaf(const char *path)
{
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static void parent_of(const char *path, char parent[PATH_MAX])
{
    const char *slash = strrchr(path, '/');
    if (!slash)
        refuse();
    size_t len = slash == path ? 1 : (size_t)(slash - path);
    memcpy(parent, path, len);
    parent[len] = '\0';
}

static size_t issue_length(const char *name)
{
    if (strncmp(name, "UIF-", 4) || name[4] < '1' || name[4] > '9')
        return 0;
    return 4 + strspn(name + 4, "0123456789");
}

static void check_attempt(const char *mode, const char *source, const char *output,
                          const char *home, const char *state)
{
    char attempt[PATH_MAX], paths[PATH_MAX], runtime[PATH_MAX], section[PATH_MAX];
    if (strcmp(leaf(output), "final-message.txt") || strcmp(leaf(state), "codex-state") ||
        strncmp(leaf(home), "codex-home-", 11) || !token(leaf(home) + 11) ||
        !under(output, UIF_OUTPUT_ROOT))
        refuse();
    parent_of(output, attempt);
    parent_of(home, paths);
    if (strcmp(attempt, paths))
        refuse();
    parent_of(state, paths);
    if (strcmp(attempt, paths))
        refuse();
    parent_of(attempt, paths);
    if (strcmp(leaf(paths), "attempts"))
        refuse();
    parent_of(paths, runtime);
    if (!under(runtime, UIF_RUNTIME_ROOT) || !strcmp(runtime, UIF_RUNTIME_ROOT))
        refuse();

    const char *attempt_name = leaf(attempt);
    int review = !strcmp(mode, "read-only");
    if (!review && strcmp(mode, "workspace-write"))
        refuse();
    if (review) {
        if (strncmp(attempt_name, "review-", 7))
            refuse();
        attempt_name += 7;
    }
    size_t n = issue_length(attempt_name);
    if (!n || (attempt_name[n] && (!review || attempt_name[n] != '-' ||
                                    !token(attempt_name + n + 1))))
        refuse();
    if (under(source, UIF_WORK_ROOT)) {
        parent_of(source, section);
        parent_of(section, paths);
        if (!strcmp(leaf(section), "workspaces") && !strcmp(paths, runtime) &&
            strlen(leaf(source)) == n && !strncmp(leaf(source), attempt_name, n))
            return;
    }
    if (review && under(source, UIF_REVIEW_ROOT) && !strcmp(leaf(source), "candidate")) {
        parent_of(source, section);
        parent_of(section, paths);
        if (!strcmp(leaf(paths), "contributions") &&
            strlen(leaf(section)) == n && !strncmp(leaf(section), attempt_name, n))
            return;
    }
    refuse();
}

static void require_absent(int dir, const char *name)
{
    struct stat info;
    if (!fstatat(dir, name, &info, AT_SYMLINK_NOFOLLOW) || errno != ENOENT)
        refuse();
}

static void check_auth(int home)
{
    struct stat info;
    char link[PATH_MAX], resolved[PATH_MAX];
    if (fstat(home, &info) || (info.st_mode & 07777) != 0700)
        refuse();
    ssize_t n = readlinkat(home, "auth.json", link, sizeof(link) - 1);
    if (n < 0 || (size_t)n != strlen(UIF_AUTH_REFERENCE) ||
        memcmp(link, UIF_AUTH_REFERENCE, (size_t)n))
        refuse();
    /* This checks the reference only. The guard never opens credential bytes. */
    if (!realpath(UIF_AUTH_REFERENCE, resolved) || strcmp(resolved, UIF_AUTH_REFERENCE) ||
        lstat(UIF_AUTH_REFERENCE, &info) || !S_ISREG(info.st_mode) || info.st_uid != UIF_UID ||
        info.st_nlink != 1 || (info.st_mode & 0077))
        refuse();
}

int main(int argc, char **argv)
{
    context();  /* Verify the post-drop context before inspecting any inputs. */
    check_pins();
    descriptors();
    if (clearenv() || argc != 6 || strcmp(argv[1], UIF_ROLE_MODE))
        refuse();
    for (int i = 2; i < argc; i++)
        if (!absolute_path(argv[i]) || !under(argv[i], UIF_RUNTIME_ROOT))
            refuse();

    check_attempt(argv[1], argv[2], argv[3], argv[4], argv[5]);
    char attempt_path[PATH_MAX];
    parent_of(argv[3], attempt_path);
    int attempt = owned_dir(attempt_path);
    require_absent(attempt, "final-message.txt");
    require_absent(attempt, "codex-state");
    close(attempt);

    int auth_home = owned_dir(argv[4]);
    check_auth(auth_home);
    close(auth_home);

    int native = native_fd();
    int workspace = owned_dir(argv[2]);
    if (fchdir(workspace))
        refuse();
    close(workspace);

    char home[PATH_MAX + 16], state[PATH_MAX + 24];
    snprintf(home, sizeof(home), "CODEX_HOME=%s", argv[4]);
    snprintf(state, sizeof(state), "CODEX_SQLITE_HOME=%s", argv[5]);
    char *env[] = {"HOME=" UIF_HOME, "USER=" UIF_PRINCIPAL, "LANG=C.UTF-8",
                   "PATH=/usr/bin:/bin", home, state, "LANGCHAIN_TRACING_V2=false",
                   "LANGSMITH_TRACING=false", "OTEL_SDK_DISABLED=true", "GIT_CONFIG_COUNT=0", NULL};
    char *args[] = {UIF_NATIVE, "exec", "--json", "--ignore-user-config", "--sandbox", argv[1],
                    "-c", "approval_policy=\"never\"", "-c", "model_reasoning_effort=\"medium\"",
                    "--model", "gpt-6.1-sol", "--cd", argv[2], "--output-last-message", argv[3],
                    "-", NULL};
    struct sock_fprog program = {(unsigned short)(sizeof(filter) / sizeof(filter[0])), filter};
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program))
        refuse();
    /* Execute the exact root-owned ELF descriptor that was just hashed. */
    execveat(native, "", args, env, AT_EMPTY_PATH);
    refuse();
}
