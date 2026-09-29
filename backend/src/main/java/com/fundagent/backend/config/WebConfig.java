package com.fundagent.backend.config;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.auth.InternalAuthInterceptor;
import com.fundagent.backend.auth.service.JwtService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 路由鉴权：/api/** 要 JWT（注册、登录、health 除外）；/internal/** 要共享密钥；springdoc 页面不拦。
 *
 * <p>本类自己不实现 WebMvcConfigurer，而是用 @Bean 方法提供：@WebMvcTest 切片会自动装入所有 WebMvcConfigurer
 * 类型的配置类，这样控制器切片测试不必关心鉴权（鉴权另有 WebConfigSecurityTest）。
 */
@Configuration
public class WebConfig {

    @Bean
    WebMvcConfigurer authWebMvcConfigurer(JwtService jwt, SecurityProperties props) {
        AuthInterceptor auth = new AuthInterceptor(jwt);
        InternalAuthInterceptor internal = new InternalAuthInterceptor(props);
        return new WebMvcConfigurer() {
            @Override
            public void addInterceptors(InterceptorRegistry registry) {
                registry.addInterceptor(auth)
                        .addPathPatterns("/api/**")
                        .excludePathPatterns("/api/auth/register", "/api/auth/login", "/api/health");
                registry.addInterceptor(internal).addPathPatterns("/internal/**");
            }
        };
    }
}
