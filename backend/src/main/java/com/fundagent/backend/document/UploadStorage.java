package com.fundagent.backend.document;

import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.UploadProperties;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/** 上传文件落盘：{dir}/{用户}/{库}/{sha256}.{ext}，先写临时文件再原子改名。ai-service 按这个绝对路径读取。 */
@Component
public class UploadStorage {

    private static final Logger log = LoggerFactory.getLogger(UploadStorage.class);

    private final Path root;

    public UploadStorage(UploadProperties props) {
        this.root = Path.of(props.dir() == null || props.dir().isBlank() ? "../data/uploads" : props.dir())
                .toAbsolutePath()
                .normalize();
    }

    public Path root() {
        return root;
    }

    /** 返回落盘后的绝对路径。 */
    public Path store(long ownerId, long kbId, String sha256, String ext, byte[] content) {
        Path dir = root.resolve(Long.toString(ownerId)).resolve(Long.toString(kbId));
        Path target = dir.resolve(sha256 + "." + ext);
        try {
            Files.createDirectories(dir);
            Path tmp = Files.createTempFile(dir, "upload-", ".tmp");
            Files.write(tmp, content);
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            return target;
        } catch (IOException e) {
            log.error("save upload failed: {}", e.toString());
            throw new BizException(ErrorCode.INTERNAL_ERROR, "保存上传文件失败");
        }
    }

    /** 尽力删除；只允许删除上传目录之内的文件。 */
    public void delete(String storagePath) {
        try {
            Path p = Path.of(storagePath).toAbsolutePath().normalize();
            if (p.startsWith(root)) {
                Files.deleteIfExists(p);
            }
        } catch (IOException | RuntimeException e) {
            log.warn("delete upload file failed: {}", e.toString());
        }
    }
}
