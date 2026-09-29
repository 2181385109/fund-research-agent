package com.fundagent.backend.document;

import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.UploadProperties;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;
import org.springframework.stereotype.Component;

/**
 * 上传校验：大小上限（20MB）、扩展名白名单（pdf / md / markdown / txt）、内容魔数——
 * PDF 必须以 %PDF- 开头；文本必须是不含 NUL 的合法 UTF-8。不信任客户端声明的 Content-Type。
 */
@Component
public class UploadValidator {

    public static final Set<String> EXTENSIONS = Set.of("pdf", "md", "markdown", "txt");
    private static final byte[] PDF_MAGIC = "%PDF-".getBytes(StandardCharsets.US_ASCII);
    static final int FILENAME_MAX = 200;

    private final long maxBytes;

    public UploadValidator(UploadProperties props) {
        this.maxBytes = props.maxSize().toBytes();
    }

    /** 通过校验的上传：净化后的文件名与小写扩展名。 */
    public record Checked(String filename, String ext) {}

    public Checked check(String originalFilename, byte[] content) {
        String filename = sanitize(originalFilename);
        String ext = extensionOf(filename);
        if (!EXTENSIONS.contains(ext)) {
            throw new BizException(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "只支持 PDF、Markdown、TXT 文件");
        }
        if (content.length == 0) {
            throw new BizException(ErrorCode.BAD_REQUEST, "文件为空");
        }
        if (content.length > maxBytes) {
            throw new BizException(ErrorCode.PAYLOAD_TOO_LARGE, "文件超过 " + (maxBytes >> 20) + "MB 上限");
        }
        if (ext.equals("pdf")) {
            if (!startsWithPdfMagic(content)) {
                throw new BizException(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "文件内容不是有效的 PDF（魔数不符）");
            }
        } else if (!isCleanUtf8(content)) {
            throw new BizException(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "文本文件必须是 UTF-8 编码且不含二进制内容");
        }
        return new Checked(filename, ext);
    }

    /** 去掉路径部分和控制字符、限制长度；结果不会为空。 */
    static String sanitize(String name) {
        String n = name == null ? "" : name;
        n = n.substring(Math.max(n.lastIndexOf('/'), n.lastIndexOf('\\')) + 1);
        n = n.replaceAll("\\p{Cntrl}", "").strip();
        if (n.length() > FILENAME_MAX) {
            int dot = n.lastIndexOf('.');
            String ext = dot >= 0 && n.length() - dot <= 10 ? n.substring(dot) : "";
            n = n.substring(0, FILENAME_MAX - ext.length()) + ext;
        }
        return n.isEmpty() ? "unnamed" : n;
    }

    static String extensionOf(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot < 0 ? "" : filename.substring(dot + 1).toLowerCase(Locale.ROOT);
    }

    private static boolean startsWithPdfMagic(byte[] c) {
        if (c.length < PDF_MAGIC.length) {
            return false;
        }
        for (int i = 0; i < PDF_MAGIC.length; i++) {
            if (c[i] != PDF_MAGIC[i]) {
                return false;
            }
        }
        return true;
    }

    private static boolean isCleanUtf8(byte[] c) {
        for (byte b : c) {
            if (b == 0) {
                return false;
            }
        }
        try {
            StandardCharsets.UTF_8
                    .newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(c));
            return true;
        } catch (CharacterCodingException e) {
            return false;
        }
    }
}
