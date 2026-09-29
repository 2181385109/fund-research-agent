package com.fundagent.backend.chat;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.chat.dto.ChatRequest;
import com.fundagent.backend.chat.service.ChatService;
import com.fundagent.backend.config.ChatProperties;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.Valid;
import java.io.IOException;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/conversations")
public class ChatController {

    private final ChatService chat;
    private final ChatProperties props;

    public ChatController(ChatService chat, ChatProperties props) {
        this.chat = chat;
        this.props = props;
    }

    @Operation(
            summary = "提问（SSE 流式）",
            description = "事件协议见 docs/API.md：meta → (tool_start/tool_end/token)* → citations → disclaimer → done。"
                    + "kbIds 缺省 = 公共库 + 自己的全部私有库；带了别人的 / 不存在的库 → 403。客户端断开即取消上游请求。")
    @PostMapping(value = "/{id}/chat", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter ask(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId,
            @PathVariable long id,
            @Valid @RequestBody ChatRequest body) {
        SseEmitter emitter = new SseEmitter(props.emitterTimeout().toMillis());
        ChatSession session = chat.start(userId, id, body.question(), body.kbIds(), new EmitterSink(emitter));
        // 连接关闭 / 超时 / 出错：取消上游。会话已正常结束时这些回调是空操作
        emitter.onCompletion(() -> session.clientGone("connection closed"));
        emitter.onTimeout(() -> session.clientGone("emitter timeout"));
        emitter.onError(e -> session.clientGone("emitter error: " + e.getClass().getSimpleName()));
        return emitter;
    }

    /** 把事件写进 SseEmitter；写失败会抛 IOException，ChatService 据此判定客户端已断开。 */
    static final class EmitterSink implements ChatSink {
        private final SseEmitter emitter;

        EmitterSink(SseEmitter emitter) {
            this.emitter = emitter;
        }

        @Override
        public void send(String event, String data) throws IOException {
            emitter.send(SseEmitter.event().name(event).data(data));
        }

        @Override
        public void heartbeat() throws IOException {
            emitter.send(SseEmitter.event().comment("ping"));
        }

        @Override
        public void complete() {
            emitter.complete();
        }
    }
}
