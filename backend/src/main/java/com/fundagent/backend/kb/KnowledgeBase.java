package com.fundagent.backend.kb;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import java.time.LocalDateTime;

/** 知识库。kb_type=PUBLIC 是系统预置的公共库（id=1，owner 为空，所有用户只读）；PRIVATE 是用户自己的库。 */
@TableName("knowledge_bases")
public class KnowledgeBase {

    public static final long PUBLIC_KB_ID = 1L;
    public static final String TYPE_PUBLIC = "PUBLIC";
    public static final String TYPE_PRIVATE = "PRIVATE";

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long ownerId;
    private String name;
    private String kbType;
    private LocalDateTime createdAt;

    public boolean isPublic() {
        return TYPE_PUBLIC.equals(kbType);
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Long getOwnerId() {
        return ownerId;
    }

    public void setOwnerId(Long ownerId) {
        this.ownerId = ownerId;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getKbType() {
        return kbType;
    }

    public void setKbType(String kbType) {
        this.kbType = kbType;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
