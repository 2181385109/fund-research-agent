package com.fundagent.backend.document;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.UploadProperties;
import com.fundagent.backend.document.UploadValidator.Checked;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.util.unit.DataSize;

class UploadValidatorTest {

    private final UploadValidator validator =
            new UploadValidator(new UploadProperties("x", DataSize.ofMegabytes(20), Duration.ofMinutes(15)));

    private static byte[] pdf() {
        return "%PDF-1.7\n1 0 obj\n<<>>\nendobj\n".getBytes(StandardCharsets.US_ASCII);
    }

    private static ErrorCode codeOf(Runnable r) {
        try {
            r.run();
        } catch (BizException e) {
            return e.errorCode();
        }
        throw new AssertionError("expected BizException");
    }

    @Test
    void acceptsPdfMarkdownAndTxtWithMatchingContent() {
        assertThat(validator.check("报告.PDF", pdf())).isEqualTo(new Checked("报告.PDF", "pdf"));
        assertThat(validator.check("notes.md", "# 标题\n正文".getBytes(StandardCharsets.UTF_8)).ext()).isEqualTo("md");
        assertThat(validator.check("a.txt", "hello".getBytes(StandardCharsets.UTF_8)).ext()).isEqualTo("txt");
        assertThat(validator.check("a.markdown", "x".getBytes(StandardCharsets.UTF_8)).ext()).isEqualTo("markdown");
    }

    @Test
    void rejectsWrongMagicNumbers() {
        // 声称是 PDF，内容不是
        assertThat(codeOf(() -> validator.check("fake.pdf", "hello world".getBytes(StandardCharsets.UTF_8))))
                .isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
        // 声称是文本，内容是 PE 可执行文件（含 NUL）
        byte[] exe = {'M', 'Z', (byte) 0x90, 0x00, 0x03, 0x00};
        assertThat(codeOf(() -> validator.check("a.txt", exe))).isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
        // PDF 冒充 txt：二进制内容不是合法 UTF-8 / 含 NUL
        byte[] binaryPdf = new byte[] {'%', 'P', 'D', 'F', '-', '1', '.', '4', '\n', '%', (byte) 0xE2, (byte) 0xE3, (byte) 0xCF, (byte) 0xD3, (byte) 0x00};
        assertThat(codeOf(() -> validator.check("a.txt", binaryPdf))).isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
    }

    @Test
    void rejectsNonUtf8Text() {
        byte[] gbk = "中文内容".getBytes(Charset.forName("GBK"));
        assertThat(codeOf(() -> validator.check("gbk.txt", gbk))).isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
    }

    @Test
    void rejectsDisallowedExtensionsAndEmptyFiles() {
        assertThat(codeOf(() -> validator.check("run.exe", pdf()))).isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
        assertThat(codeOf(() -> validator.check("noext", pdf()))).isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
        assertThat(codeOf(() -> validator.check("a.pdf", new byte[0]))).isEqualTo(ErrorCode.BAD_REQUEST);
        assertThat(codeOf(() -> validator.check(null, pdf()))).isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
    }

    @Test
    void enforcesTwentyMegabyteLimit() {
        UploadValidator small = new UploadValidator(new UploadProperties("x", DataSize.ofBytes(100), Duration.ofMinutes(1)));
        byte[] big = new byte[101];
        System.arraycopy(pdf(), 0, big, 0, pdf().length);
        assertThat(codeOf(() -> small.check("a.pdf", big))).isEqualTo(ErrorCode.PAYLOAD_TOO_LARGE);
        byte[] ok = new byte[100];
        System.arraycopy(pdf(), 0, ok, 0, pdf().length);
        assertThat(small.check("a.pdf", ok).ext()).isEqualTo("pdf");
        assertThat(validator).isNotNull();
    }

    @Test
    void sanitizesFilenames() {
        assertThat(UploadValidator.sanitize("../../etc/passwd.txt")).isEqualTo("passwd.txt");
        assertThat(UploadValidator.sanitize("..\\..\\up\\我的文件.pdf")).isEqualTo("我的文件.pdf");
        assertThat(UploadValidator.sanitize("a\u0000b\nc.md")).isEqualTo("abc.md");
        assertThat(UploadValidator.sanitize("")).isEqualTo("unnamed");
        String longName = "x".repeat(500) + ".pdf";
        String cut = UploadValidator.sanitize(longName);
        assertThat(cut).hasSize(UploadValidator.FILENAME_MAX).endsWith(".pdf");
        assertThatThrownBy(() -> validator.check("dir/evil.sh", pdf())).isInstanceOf(BizException.class);
    }
}
