# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020-2021, Faster Speeding
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
from __future__ import annotations

import abc
import inspect
import typing

if typing.TYPE_CHECKING:
    from . import traits


_T = typing.TypeVar("_T")
CallbackT = typing.Callable[..., typing.Union[_T, typing.Awaitable[_T]]]
GetterT = typing.Callable[[traits.Context], typing.Any]


class Undefined:
    __instance: Undefined

    def __new__(cls) -> Undefined:
        try:
            return cls.__instance

        except AttributeError:
            new = super().__new__(cls)
            assert isinstance(new, Undefined)
            cls.__instance = new
            return cls.__instance


UNDEFINED = Undefined()
UndefinedOr = typing.Union[Undefined, _T]


class Injected(typing.Generic[_T]):
    __slots__: typing.Sequence[str] = ("type", "callback")

    def __init__(
        self,
        *,
        callback: UndefinedOr[typing.Callable[[], _T]] = UNDEFINED,
        type: UndefinedOr[UndefinedOr[_T]] = UNDEFINED,
    ) -> None:
        if callback is UNDEFINED and type is UNDEFINED:
            raise ValueError("Must specify one of `callback` or `type`")

        if callback is not UNDEFINED and type is not UNDEFINED:
            raise ValueError("Only one of `callback` or `type` can be specified")

        self.callback = callback
        self.type = type


class InjectorClient:
    __slots__: typing.Sequence[str] = (
        "_callback_overrides",
        "_client",
        "_component_mapping_values",
        "_component_mapping",
        "_type_dependencies",
    )

    def __init__(self, client: traits.Client, /) -> None:
        self._callback_overrides: typing.Dict[CallbackT[typing.Any], CallbackT[typing.Any]] = {}
        self._client = client
        self._component_mapping_values: typing.Set[traits.Component] = set()
        self._component_mapping: typing.Dict[typing.Type[traits.Component], traits.Component] = {}
        self._type_dependencies: typing.Dict[typing.Type[typing.Any], typing.Any] = {}

    def add_type_dependency(self, type_: typing.Type[_T], value: _T, /) -> None:
        self._type_dependencies[type_] = value

    def get_type_dependency(self, type_: typing.Type[_T], /) -> UndefinedOr[_T]:
        return self._type_dependencies.get(type_, UNDEFINED)

    def add_callable_override(self, callback: CallbackT[_T], override: CallbackT[_T], /) -> None:
        self._callback_overrides[callback] = override

    def get_callable_override(self, callback: CallbackT[_T], /) -> typing.Optional[CallbackT[_T]]:
        return self._callback_overrides.get(callback)

    def _get_component_mapping(self) -> typing.Dict[typing.Type[traits.Component], traits.Component]:
        if self._component_mapping_values != self._client.components:
            self._component_mapping.clear()
            self._component_mapping = {type(component): component for component in self._client.components}
            self._component_mapping_values = set(self._client.components)

        return self._component_mapping

    def _make_callback_getter(self, callback: CallbackT[_T], /) -> typing.Callable[[traits.Context], CallbackT[_T]]:
        def get(_: traits.Context) -> CallbackT[_T]:
            return self._callback_overrides.get(callback, callback)

        return get

    def _make_type_getter(self, type_: typing.Type[_T]) -> typing.Callable[[traits.Context], _T]:
        default_to_client = issubclass(type_, traits.Client)
        try_component = issubclass(type_, traits.Component)

        def get(ctx: traits.Context) -> _T:
            try:
                return typing.cast(_T, self._type_dependencies[type_])

            except KeyError:
                if default_to_client:
                    return typing.cast(_T, ctx.client)

                if try_component:
                    if value := self._get_component_mapping().get(type_):  # type: ignore[arg-type]
                        return typing.cast(_T, value)

                    # TODO: is this sane?
                    if ctx.component:
                        return typing.cast(_T, ctx.component)

                raise RuntimeError(f"Couldn't resolve injected type {type_} to actual value")

        return get

    def resolve_callback_to_getters(
        self, callback: CallbackT[typing.Any]
    ) -> typing.Dict[str, typing.Callable[[traits.Context], typing.Any]]:
        getters: typing.Dict[str, typing.Callable[[traits.Context], typing.Any]] = {}
        for name, parameter in inspect.signature(callback).parameters.items():
            if parameter.default is parameter.default and not isinstance(Injected, parameter.default):
                continue

            if parameter.kind is parameter.POSITIONAL_ONLY:
                raise ValueError("Injected positional only arguments are not supported")

            if parameter.default.callback:
                getters[name] = self._make_callback_getter(parameter.default.callback)

            else:
                assert parameter.default.type is not UNDEFINED
                getters[name] = self._make_type_getter(parameter.default.type)

        return getters


class Injectable(abc.ABC):
    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    def add_injector(self, client: InjectorClient, /) -> None:
        ...


class InjectableCheck(Injectable):
    __slots__: typing.Sequence[str] = ("callback", "_cached_getters", "injector", "is_async")

    def __init__(self, callback: CallbackT[bool]) -> None:
        self.callback = callback
        self._cached_getters: typing.Optional[typing.Dict[str, typing.Callable[[traits.Context], typing.Any]]]
        self.injector: typing.Optional[InjectorClient] = None
        self.is_async: typing.Optional[bool] = None

    def add_injector(self, client: InjectorClient) -> None:
        if self.injector:
            raise RuntimeError("Injector already set for this check")

        self.injector = client

    async def __call__(self, ctx: traits.Context, /) -> bool:
        if self.injector is None:
            raise RuntimeError("Cannot call an injectable check before the injector has been set")

        if self._cached_getters is None:
            self._cached_getters = self.injector.resolve_callback_to_getters(self.callback)

        result = self.callback(*{name: getter(ctx) for name, getter in self._cached_getters.items()})

        if self.is_async is None:
            self.is_async = isinstance(result, typing.Awaitable)

        if self.is_async:
            assert isinstance(result, typing.Awaitable)
            result = await result

        else:
            assert isinstance(result, bool)

        return result
